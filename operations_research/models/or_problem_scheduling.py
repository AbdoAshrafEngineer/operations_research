import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..tools import linear

from ..tools import scheduler

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
        [('minutes', 'Minutes'), ('hours', 'Hours'), ('days', 'Days (24h)')],
        string='Duration Unit', default='hours', required=True)
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

    def _or_sched_origin(self):
        origin = self.sched_start_date if self.sched_start_mode == 'fixed' else fields.Datetime.now()
        return origin.replace(second=0, microsecond=0)

    @staticmethod
    def _or_minutes(delta):
        return int(delta.total_seconds() // 60)

    def _or_solve_scheduling(self, run):
        records = self._or_get_records()
        self._or_check_size(len(records) * 3)
        origin = self._or_sched_origin()
        factor = UNIT_MINUTES[self.sched_duration_unit]
        ids = set(records.ids)
        tasks, negative, impossible, log = [], [], [], []
        predecessors = {}
        for rec in records:
            duration = self._or_num(rec, self.sched_duration_field_id, 0.0)
            if duration < 0:
                negative.append(rec.display_name)
            minutes = int(round(duration * factor))
            release = 0
            if self.sched_release_field_id and rec[self.sched_release_field_id.name]:
                release = max(0, self._or_minutes(rec[self.sched_release_field_id.name] - origin))
            deadline = None
            if self.sched_deadline_field_id and rec[self.sched_deadline_field_id.name]:
                deadline = self._or_minutes(rec[self.sched_deadline_field_id.name] - origin)
                if self.sched_deadline_mode == 'hard' and deadline < release + minutes:
                    impossible.append(rec.display_name)
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
            tasks.append({'key': rec.id, 'duration': max(minutes, 0), 'release': release,
                          'deadline': deadline, 'resource': resource, 'weight': weight,
                          'predecessors': preds})
        if negative:
            raise UserError(self.env._("Negative duration for: %s", ', '.join(negative[:10])))
        cycle = scheduler.find_cycle(predecessors)
        if cycle:
            names = dict(zip(records.ids, records.mapped('display_name')))
            raise UserError(self.env._("Circular dependency between: %s",
                                       ' → '.join(names[key] for key in cycle)))
        if impossible:
            log.append(self.env._("Deadline earlier than earliest start + duration for: %s",
                                  ', '.join(impossible[:20])))
        kwargs = self._or_solver_kwargs()
        result = scheduler.solve_schedule(
            tasks, objective=self.sched_objective, deadline_mode=self.sched_deadline_mode,
            time_limit=kwargs['time_limit'], num_workers=kwargs['num_threads'],
            mip_gap=self.mip_gap / 100.0 if self.mip_gap else None)
        snapshot = self._or_snapshot_scheduling(records)
        lines = []
        if result['status'] in linear.SOLUTION_STATUSES:
            start_name = self.sched_start_field_id.name
            res_field = self.sched_resource_field_id.name if self.sched_resource_field_id else None
            order = sorted(records, key=lambda r: (result['starts'][r.id], r.id))
            for sequence, rec in enumerate(order, 1):
                start = origin + timedelta(minutes=result['starts'][rec.id])
                end = origin + timedelta(minutes=result['ends'][rec.id])
                lines.append({
                    'sequence': sequence,
                    'res_model': self.model_name,
                    'res_id': rec.id,
                    'res_name': rec.display_name,
                    'sched_start': start,
                    'sched_end': end,
                    'sched_start_before': rec[start_name] or False,
                    'sched_late_minutes': result['lateness'].get(rec.id, 0),
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
        objective_hours = result['objective'] / 60.0
        result.update({
            'objective': objective_hours,
            'bound': result['bound'] / 60.0,
            'lines': lines,
            'constraint_lines': [],
            'log': log,
        })
        return result

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
