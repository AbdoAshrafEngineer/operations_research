import logging
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..tools import linear

from ..tools import scheduler
from ..tools.workcal import WorkAxis

_logger = logging.getLogger(__name__)

UNIT_MINUTES = {'minutes': 1, 'hours': 60, 'days': 1440}


class OrProblem(models.Model):
    _inherit = 'or.problem'

    problem_type = fields.Selection(selection_add=[('scheduling', 'Scheduling')],
                                    ondelete={'scheduling': 'cascade'})
    sched_duration_field_id = fields.Many2one(
        'ir.model.fields', string='Duration Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    sched_duration_unit = fields.Selection(
        [('minutes', 'Minutes'), ('hours', 'Hours'), ('days', 'Days')],
        string='Duration Unit', default='hours', required=True,
        help="Days are 24 hours around the clock, or one working day of the chosen working "
             "hours (for example 8 hours) when planning within working hours.")
    sched_time_mode = fields.Selection(
        [('continuous', 'Around the clock (24/7)'), ('calendar', 'Working hours only')],
        string='Working Time', default='continuous', required=True,
        help="Working hours only: jobs are planned inside the working hours and public holidays "
             "of the chosen calendar; a job that does not finish today continues on the next "
             "working day.")
    sched_calendar_id = fields.Many2one(
        'resource.calendar', string='Working Hours', ondelete='restrict',
        default=lambda self: self.env.company.resource_calendar_id)
    sched_resource_field_id = fields.Many2one(
        'ir.model.fields', string='Resource Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'many2one')]",
        help="Records sharing the same resource (machine, employee...) never overlap. "
             "Records without a resource are not limited.")
    sched_release_field_id = fields.Many2one(
        'ir.model.fields', string='Earliest Start Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'datetime')]")
    sched_deadline_field_id = fields.Many2one(
        'ir.model.fields', string='Deadline Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'datetime')]")
    sched_deadline_mode = fields.Selection(
        [('soft', 'Soft (penalised when late)'), ('hard', 'Hard (must be met)')],
        string='Deadlines', default='soft', required=True)
    sched_weight_field_id = fields.Many2one(
        'ir.model.fields', string='Priority Weight Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    sched_predecessor_field_id = fields.Many2one(
        'ir.model.fields', string='Predecessors Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('many2one', 'many2many'))]",
        help="Relational field on the same model listing the records that must finish first.")
    sched_start_field_id = fields.Many2one(
        'ir.model.fields', string='Write Start To', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'datetime')]")
    sched_end_field_id = fields.Many2one(
        'ir.model.fields', string='Write End To', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'datetime')]")
    sched_objective = fields.Selection(
        [('makespan', 'Finish everything as early as possible'),
         ('tardiness', 'Minimise weighted lateness'),
         ('completion', 'Minimise weighted completion time')],
        string='Scheduling Goal', default='makespan', required=True)
    sched_start_mode = fields.Selection(
        [('now', 'When solving'), ('fixed', 'Fixed date')], string='Plan From',
        default='now', required=True)
    sched_start_date = fields.Datetime(string='Plan From Date')

    def _or_model_field_names(self):
        return super()._or_model_field_names() + [
            'sched_duration_field_id', 'sched_resource_field_id', 'sched_release_field_id',
            'sched_deadline_field_id', 'sched_weight_field_id', 'sched_predecessor_field_id',
            'sched_start_field_id', 'sched_end_field_id']

    def _or_signature_fields(self):
        return super()._or_signature_fields() + ['sched_start_field_id', 'sched_end_field_id']

    @api.constrains('sched_predecessor_field_id', 'sched_start_field_id', 'sched_end_field_id')
    def _check_sched_fields(self):
        for problem in self:
            pred = problem.sched_predecessor_field_id
            if pred and pred.relation != problem.model_name:
                raise ValidationError(self.env._(
                    "The predecessors field must point to the same model (%s).", problem.model_name))
            for field in (problem.sched_start_field_id, problem.sched_end_field_id):
                if field:
                    problem._check_writable_field(field)
            if problem.sched_start_field_id and problem.sched_start_field_id == problem.sched_end_field_id:
                raise ValidationError(self.env._("Start and end must be written to different fields."))

    def _or_estimate_size_scheduling(self, count):
        return count * 3

    def _or_check_scheduling(self):
        if not self.sched_duration_field_id:
            raise UserError(self.env._("Choose the duration field of problem %s.", self.name))
        if not self.sched_start_field_id:
            raise UserError(self.env._("Choose the field receiving the planned start."))
        if self.sched_objective == 'tardiness' and not self.sched_deadline_field_id:
            raise UserError(self.env._("Minimising lateness requires a deadline field."))
        if self.sched_start_mode == 'fixed' and not self.sched_start_date:
            raise UserError(self.env._("Choose the date to plan from."))
        if self.sched_time_mode == 'calendar' and not self.sched_calendar_id:
            raise UserError(self.env._("Choose the working hours to plan within."))

    def _or_sched_origin(self):
        origin = self.sched_start_date if self.sched_start_mode == 'fixed' else fields.Datetime.now()
        return origin.replace(second=0, microsecond=0)

    @staticmethod
    def _or_minutes(delta):
        return int(delta.total_seconds() // 60)

    def _or_sched_minutes_per_unit(self):
        if self.sched_duration_unit == 'days' and self.sched_time_mode == 'calendar':
            return int(round((self.sched_calendar_id.hours_per_day or 8.0) * 60))
        return UNIT_MINUTES[self.sched_duration_unit]

    def _or_sched_work_axis(self, origin, last_date, needed):
        """Working-time axis long enough for ``needed(axis)`` working minutes.

        Public holidays and closures of the calendar are excluded.
        """
        calendar = self.sched_calendar_id
        # Working hours are local times. Odoo 19 reads the zone on the calendar; Odoo 20 calendars
        # have none and use the zone of the dates passed in: give them the user's zone.
        local = ZoneInfo(getattr(calendar, 'tz', False) or self.env.user.tz or 'UTC')
        span = max(last_date - origin, timedelta(0)) + timedelta(days=60)
        while True:
            end = origin + span
            intervals = calendar._work_intervals_batch(
                origin.replace(tzinfo=timezone.utc).astimezone(local),
                end.replace(tzinfo=timezone.utc).astimezone(local))[False]
            axis = WorkAxis([
                (start.astimezone(timezone.utc).replace(tzinfo=None),
                 stop.astimezone(timezone.utc).replace(tzinfo=None))
                for start, stop, _records in intervals], origin)
            if axis.total > needed(axis):
                return axis
            if span > timedelta(days=3 * 366):
                raise UserError(self.env._(
                    "The working hours %(calendar)s leave too little time to plan these jobs "
                    "(checked until %(date)s). Check the calendar or plan around the clock.",
                    calendar=calendar.display_name, date=end.date()))
            span *= 2

    def _or_solve_scheduling(self, run):
        records = self._or_get_records()
        self._or_check_size(len(records) * 3)
        origin = self._or_sched_origin()
        factor = self._or_sched_minutes_per_unit()
        ids = set(records.ids)
        raw, negative, bad_weight, log = [], [], [], []
        predecessors = {}
        for rec in records:
            duration = self._or_num(rec, self.sched_duration_field_id, 0.0)
            if duration < 0:
                negative.append(rec.display_name)
            release_dt = rec[self.sched_release_field_id.name] if self.sched_release_field_id else False
            deadline_dt = rec[self.sched_deadline_field_id.name] if self.sched_deadline_field_id else False
            resource = None
            if self.sched_resource_field_id:
                resource = rec[self.sched_resource_field_id.name].id or None
            preds = []
            if self.sched_predecessor_field_id:
                for pred in rec[self.sched_predecessor_field_id.name]:
                    if pred.id in ids:
                        preds.append(pred.id)
                    else:
                        log.append(self.env._("%(task)s: predecessor %(pred)s is outside the problem "
                                              "and is ignored.", task=rec.display_name,
                                              pred=pred.display_name))
            predecessors[rec.id] = preds
            weight = self._or_num(rec, self.sched_weight_field_id, 1.0) if self.sched_weight_field_id else 1.0
            if weight < 0:
                bad_weight.append(rec.display_name)
            raw.append({'rec': rec, 'minutes': max(int(round(duration * factor)), 0),
                        'release_dt': release_dt or None, 'deadline_dt': deadline_dt or None,
                        'resource': resource, 'weight': weight, 'preds': preds})
        if negative:
            raise UserError(self.env._("Negative duration for: %s", ', '.join(negative[:10])))
        if bad_weight:
            raise UserError(self.env._("Negative priority weight for: %s", ', '.join(bad_weight[:10])))
        cycle = scheduler.find_cycle(predecessors)
        if cycle:
            names = dict(zip(records.ids, records.mapped('display_name')))
            raise UserError(self.env._("Circular dependency between: %s",
                                       ' → '.join(names[key] for key in cycle)))

        axis = None
        if self.sched_time_mode == 'calendar':
            dates = [d for item in raw for d in (item['release_dt'], item['deadline_dt']) if d]
            total_work = sum(item['minutes'] for item in raw)

            def needed(work_axis):
                releases = [work_axis.to_work(item['release_dt']) for item in raw if item['release_dt']]
                return max(releases, default=0) + total_work + 1
            axis = self._or_sched_work_axis(origin, max(dates, default=origin), needed)
            to_minutes = axis.to_work
            log.append(self.env._("Planned within the working hours of %s.",
                                  self.sched_calendar_id.display_name))
        else:
            def to_minutes(moment):
                return self._or_minutes(moment - origin)

        tasks, impossible = [], []
        for item in raw:
            release = max(0, to_minutes(item['release_dt'])) if item['release_dt'] else 0
            deadline = to_minutes(item['deadline_dt']) if item['deadline_dt'] else None
            if (self.sched_deadline_mode == 'hard' and deadline is not None
                    and deadline < release + item['minutes']):
                impossible.append(item['rec'].display_name)
            tasks.append({'key': item['rec'].id, 'duration': item['minutes'], 'release': release,
                          'deadline': deadline, 'resource': item['resource'],
                          'weight': item['weight'], 'predecessors': item['preds']})
        if impossible:
            log.append(self.env._("Deadline earlier than earliest start + duration for: %s",
                                  ', '.join(impossible[:20])))
        kwargs = self._or_solver_kwargs()
        result = scheduler.solve_schedule(
            tasks, objective=self.sched_objective, deadline_mode=self.sched_deadline_mode,
            time_limit=kwargs['time_limit'], num_workers=kwargs['num_threads'],
            mip_gap=self.mip_gap / 100.0 if self.mip_gap else None)
        deadlines = {item['rec'].id: item['deadline_dt'] for item in raw}
        durations = {item['rec'].id: item['minutes'] for item in raw}

        def real_times(key):
            start_m, end_m = result['starts'][key], result['ends'][key]
            if axis is None:
                return origin + timedelta(minutes=start_m), origin + timedelta(minutes=end_m)
            start = axis.to_real_start(start_m)
            return start, (axis.to_real_end(end_m) if durations[key] else start)
        snapshot = self._or_snapshot_scheduling(records)
        lines = []
        if result['status'] in linear.SOLUTION_STATUSES:
            start_name = self.sched_start_field_id.name
            res_field = self.sched_resource_field_id.name if self.sched_resource_field_id else None
            order = sorted(records, key=lambda r: (result['starts'][r.id], r.id))
            for sequence, rec in enumerate(order, 1):
                start, end = real_times(rec.id)
                deadline_dt = deadlines[rec.id]
                late = max(0, self._or_minutes(end - deadline_dt)) if deadline_dt else 0
                lines.append({
                    'sequence': sequence,
                    'res_model': self.model_name,
                    'res_id': rec.id,
                    'res_name': rec.display_name,
                    'sched_start': start,
                    'sched_end': end,
                    'sched_start_before': rec[start_name] or False,
                    'sched_late_minutes': late,
                    'assigned_name': rec[res_field].display_name if res_field else False,
                    'assigned_res_id': rec[res_field].id if res_field else 0,
                    'snapshot': snapshot[rec.id],
                })
            late = [line['res_name'] for line in lines if line['sched_late_minutes'] > 0]
            if late:
                log.append(self.env._("Late: %s", ', '.join(late[:20])))
        elif result['status'] == linear.INFEASIBLE and self.sched_deadline_mode == 'hard':
            log.append(self.env._("Hard deadlines cannot all be met. Switch deadlines to soft to see "
                                  "the best compromise."))
        kpis = self._or_sched_kpis(records, lines, origin, durations, deadlines) if lines else []
        objective_hours = result['objective'] / 60.0
        result.update({
            'objective': objective_hours,
            'bound': result['bound'] / 60.0,
            'lines': lines,
            'constraint_lines': [],
            'log': log,
            'kpis': kpis,
        })
        return result

    def _or_sched_kpis(self, records, lines, origin, durations, deadlines):
        """Compare with the dates the records have now (when they all have one)."""
        _ = self.env._
        start_name = self.sched_start_field_id.name
        end_name = self.sched_end_field_id.name if self.sched_end_field_id else None
        current_end = {}
        for rec in records:
            start = rec[start_name]
            if not start:
                continue
            end = rec[end_name] if end_name and rec[end_name] else start + timedelta(minutes=durations[rec.id])
            current_end[rec.id] = max(end, start)
        comparable = len(current_end) == len(records)
        proposed_end = {line['res_id']: line['sched_end'] for line in lines}
        broken = self._or_sched_broken_rules(records, current_end, deadlines) if comparable else []

        def hours(moment):
            return round((moment - origin).total_seconds() / 3600.0, 2)

        if broken:
            comparable = False  # a plan that breaks the rules is not a fair comparison
        kpis = [self._or_kpi(_("Hours until everything is done"),
                             hours(max(current_end.values())) if comparable else 0.0,
                             hours(max(proposed_end.values())), unit=_("h"), has_baseline=comparable)]
        if any(deadlines.values()):
            def late(ends):
                late_hours = [max(0.0, (ends[key] - deadline).total_seconds() / 3600.0)
                              for key, deadline in deadlines.items() if deadline and key in ends]
                return sum(1 for h in late_hours if h > 0), round(sum(late_hours), 2)
            late_before, hours_before = late(current_end) if comparable else (0, 0.0)
            late_after, hours_after = late(proposed_end)
            late_kpis = [self._or_kpi(_("Hours late"), hours_before, hours_after, unit=_("h"),
                                      has_baseline=comparable),
                         self._or_kpi(_("Late jobs"), late_before, late_after, has_baseline=comparable)]
            kpis = late_kpis + kpis if self.sched_objective == 'tardiness' else kpis + late_kpis
        if broken:
            kpis.insert(0, self._or_kpi_broken_plan(broken))
        return kpis

    def _or_sched_broken_rules(self, records, current_end, deadlines):
        """Rules the current dates break: overlaps, order, earliest starts, hard deadlines."""
        _ = self.env._
        start_name = self.sched_start_field_id.name
        broken = []
        if self.sched_resource_field_id:
            by_resource = {}
            for rec in records:
                resource = rec[self.sched_resource_field_id.name]
                if resource:
                    by_resource.setdefault(resource, []).append((rec[start_name], current_end[rec.id]))
            for resource, intervals in by_resource.items():
                intervals.sort()
                if any(nxt[0] < prev[1] for prev, nxt in zip(intervals, intervals[1:])):
                    broken.append(_("%s does two jobs at once", resource.display_name))
        if self.sched_predecessor_field_id:
            ids = set(records.ids)
            if any(rec[start_name] < current_end[pred.id]
                   for rec in records for pred in rec[self.sched_predecessor_field_id.name] if pred.id in ids):
                broken.append(_("job order"))
        if self.sched_release_field_id and any(
                rec[self.sched_release_field_id.name] and rec[start_name] < rec[self.sched_release_field_id.name]
                for rec in records):
            broken.append(_("earliest start"))
        if self.sched_deadline_mode == 'hard' and any(
                deadline and current_end[key] > deadline for key, deadline in deadlines.items()):
            broken.append(_("hard deadline"))
        return broken

    def _or_written_fields_scheduling(self):
        return [self.sched_start_field_id.name, self.sched_end_field_id.name]

    def _or_snapshot_scheduling(self, records):
        start = self.sched_start_field_id.name
        end = self.sched_end_field_id.name if self.sched_end_field_id else None
        return {rec.id: '%s|%s' % (rec[start] or '', rec[end] or '' if end else '') for rec in records}

    def _or_apply_scheduling(self, run, lines):
        Model = self._or_get_model()
        start = self.sched_start_field_id.name
        end = self.sched_end_field_id.name if self.sched_end_field_id else None
        for line in lines:
            vals = {start: line.sched_start}
            if end:
                vals[end] = line.sched_end
            Model.browse(line.res_id).write(vals)
