"""Check Setup: tell the user, before solving, what is missing or will go wrong."""
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools import scheduler
from .or_problem import field_value_key

BLOCKER, WARNING, INFO, OK = 'blocker', 'warning', 'info', 'ok'
LEVELS = [(BLOCKER, 'Must fix'), (WARNING, 'Check'), (INFO, 'Good to know'), (OK, 'Ready')]
MAX_NAMES = 8


def names(records_or_names):
    items = list(records_or_names)
    shown = [item if isinstance(item, str) else item.display_name for item in items[:MAX_NAMES]]
    more = len(items) - len(shown)
    return ', '.join(shown) + (' (+%s)' % more if more > 0 else '')


class OrSetupCheck(models.TransientModel):
    _name = 'or.setup.check'
    _description = 'Optimization Setup Check'

    problem_id = fields.Many2one('or.problem', required=True, ondelete='cascade')
    line_ids = fields.One2many('or.setup.check.line', 'check_id', string='Checks')
    blocker_count = fields.Integer(compute='_compute_counts')
    warning_count = fields.Integer(compute='_compute_counts')
    is_ready = fields.Boolean(compute='_compute_counts')

    @api.depends('line_ids.level')
    def _compute_counts(self):
        for check in self:
            levels = check.line_ids.mapped('level')
            check.blocker_count = levels.count(BLOCKER)
            check.warning_count = levels.count(WARNING)
            check.is_ready = not check.blocker_count

    def action_solve(self):
        self.ensure_one()
        if not self.is_ready:
            raise UserError(self.env._("Fix the items marked 'Must fix' first."))
        return self.problem_id.action_run()


class OrSetupCheckLine(models.TransientModel):
    _name = 'or.setup.check.line'
    _description = 'Optimization Setup Check Line'
    _order = 'sequence, id'

    check_id = fields.Many2one('or.setup.check', required=True, ondelete='cascade')
    sequence = fields.Integer()
    level = fields.Selection(LEVELS, required=True)
    message = fields.Text(required=True)


class OrProblem(models.Model):
    _inherit = 'or.problem'

    def action_check_setup(self):
        self.ensure_one()
        order = {BLOCKER: 0, WARNING: 1, INFO: 2, OK: 3}
        checks = sorted(self._or_setup_checks(), key=lambda item: order[item[0]])
        wizard = self.env['or.setup.check'].create({
            'problem_id': self.id,
            'line_ids': [fields.Command.create({'sequence': i, 'level': level, 'message': message})
                         for i, (level, message) in enumerate(checks)],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Check Setup: %s", self.name),
            'res_model': 'or.setup.check',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _or_setup_checks(self):
        """List of ``(level, message)``; never raises for configuration or data problems."""
        self.ensure_one()
        _ = self.env._
        try:
            self._or_check_config()
        except (UserError, ValidationError) as e:
            return [(BLOCKER, str(e.args[0] if e.args else e))]
        try:
            records = self._or_get_records(raise_if_empty=False)
        except (UserError, AccessError) as e:
            return [(BLOCKER, str(e.args[0] if e.args else e))]
        if not records:
            return [(BLOCKER, _("No %(model)s record matches the records filter. Change the filter.",
                                model=self.model_id.name))]
        checks = [(OK, _("%(count)s %(model)s record(s) to plan.", count=len(records), model=self.model_id.name))]
        try:
            size = self._or_estimate_size()
            self._or_check_size(size)
        except UserError as e:
            return checks + [(BLOCKER, str(e.args[0]))]
        if size > self._or_sync_max_variables() or not self._or_can_solve_now():
            checks.append((INFO, _("This problem will be solved in the background; you get a "
                                   "notification when it is done.")))
        if self._or_effective_time_limit() < (self.time_limit or 30):
            checks.append((WARNING, _(
                "The time limit (%(asked)s s) is above what the server allows; it will be "
                "reduced to %(used)s s.", asked=self.time_limit, used=self._or_effective_time_limit())))
        method = getattr(self, '_or_setup_checks_%s' % self.problem_type, None)
        if method:
            try:
                checks += method(records)
            except (UserError, ValidationError) as e:
                checks.append((BLOCKER, str(e.args[0] if e.args else e)))
        if not any(level == BLOCKER for level, _msg in checks):
            checks.append((OK, _("Ready to solve.")))
        return checks

    # ------------------------------------------------------------------
    def _or_setup_checks_lp(self, records):
        _ = self.env._
        checks = []
        if self.objective_path:
            zero = records.filtered(lambda rec: not self._or_path_num(rec, self.objective_path, 0.0))
            if zero:
                checks.append((WARNING, _("%(count)s record(s) have no value for the goal (counted as 0): %(names)s",
                                          count=len(zero), names=names(zero))))
        else:
            checks.append((WARNING, _("No goal value is set: every solution is equally good.")))
        if self.ub_field_id:
            empty = records.filtered(lambda rec: not rec[self.ub_field_id.name])
            if empty and self.var_type != 'binary':
                checks.append((WARNING, _("%(count)s record(s) have an empty maximum, so they can get 0 at most: %(names)s",
                                          count=len(empty), names=names(empty))))
        for line in self.constraint_ids.filtered('active').sorted('sequence'):
            log = []
            rows = line._or_build_rows(records, log)
            if not any(row['coefs'] for row in rows):
                checks.append((WARNING, _("Limit %s applies to no record.", line.name)))
            for message in log:
                checks.append((WARNING, '%s: %s' % (line.name, message)))
        if not self.constraint_ids.filtered('active') and self.ub_infinite and not self.ub_field_id:
            checks.append((BLOCKER, _("There is no limit and no maximum: the best plan would be infinite. "
                                      "Add a limit or a maximum.")))
        return checks

    def _or_setup_checks_assignment(self, records):
        _ = self.env._
        checks = []
        resources = self._or_get_resources()
        checks.append((OK, _("%s resource(s) available.", len(resources))))
        loads = {task.id: (self._or_num(task, self.assign_task_load_field_id, 1.0)
                           if self.assign_task_load_field_id else 1.0) for task in records}
        if self.assign_capacity_field_id:
            capacities = {res.id: self._or_num(res, self.assign_capacity_field_id, 0.0) for res in resources}
            empty = resources.filtered(lambda res: capacities[res.id] <= 0)
            if empty:
                checks.append((WARNING, _("%(count)s resource(s) have no capacity and get nothing: %(names)s",
                                          count=len(empty), names=names(empty))))
            total_load, total_cap = sum(loads.values()), sum(capacities.values())
            if total_load > total_cap + 1e-9:
                level = BLOCKER if self.assign_all else WARNING
                checks.append((level, _(
                    "Total load %(load)s is more than total capacity %(cap)s.", load=round(total_load, 2),
                    cap=round(total_cap, 2)) + ' ' + (_(
                        "Raise capacities, add resources or untick 'Every Record Must Be Assigned'.")
                        if self.assign_all else _("Some records will stay unassigned."))))
            biggest = max(capacities.values(), default=0)
            too_big = records.filtered(lambda task: loads[task.id] > biggest + 1e-9)
            if too_big:
                checks.append((BLOCKER if self.assign_all else WARNING, _(
                    "These records need more than any resource has (%(cap)s): %(names)s",
                    cap=round(biggest, 2), names=names(too_big))))
        elif len(records) > len(resources):
            checks.append((BLOCKER if self.assign_all else WARNING, _(
                "Without a capacity field each resource takes one record: %(tasks)s records but "
                "%(res)s resources. Choose a capacity field or add resources.",
                tasks=len(records), res=len(resources))))
        if self.assign_task_load_field_id:
            zero = records.filtered(lambda task: not loads[task.id])
            if zero:
                checks.append((INFO, _("%(count)s record(s) have no load and fit anywhere: %(names)s",
                                       count=len(zero), names=names(zero))))
        if self.assign_cost_mode != 'constant' and self.assign_resource_cost_field_id:
            if not any(self._or_num(res, self.assign_resource_cost_field_id) for res in resources):
                checks.append((WARNING, _("No resource has a cost: every choice costs the same.")))
        if (self.assign_task_match_field_id and self.assign_resource_match_field_id
                and self.assign_mismatch_policy == 'forbid'):
            keys = {field_value_key(res[self.assign_resource_match_field_id.name]) for res in resources}
            orphans = records.filtered(
                lambda task: field_value_key(task[self.assign_task_match_field_id.name]) not in keys)
            if orphans:
                checks.append((BLOCKER if self.assign_all else WARNING, _(
                    "No matching resource for: %s", names(orphans))))
        return checks

    def _or_setup_checks_scheduling(self, records):
        _ = self.env._
        checks = []
        factor = self._or_sched_minutes_per_unit()
        durations = {rec.id: self._or_num(rec, self.sched_duration_field_id, 0.0) for rec in records}
        negative = records.filtered(lambda rec: durations[rec.id] < 0)
        if negative:
            checks.append((BLOCKER, _("Negative duration for: %s", names(negative))))
        zero = records.filtered(lambda rec: not durations[rec.id])
        if zero:
            checks.append((WARNING, _("%(count)s record(s) have no duration and take no time: %(names)s",
                                      count=len(zero), names=names(zero))))
        if self.sched_weight_field_id:
            bad = records.filtered(lambda rec: self._or_num(rec, self.sched_weight_field_id, 1.0) < 0)
            if bad:
                checks.append((BLOCKER, _("Negative priority weight for: %s", names(bad))))
        if self.sched_resource_field_id:
            free = records.filtered(lambda rec: not rec[self.sched_resource_field_id.name])
            if free:
                checks.append((INFO, _("%(count)s record(s) have no %(field)s and may run at the same time "
                                       "as anything: %(names)s", count=len(free),
                                       field=self.sched_resource_field_id.field_description, names=names(free))))
        if self.sched_predecessor_field_id:
            ids = set(records.ids)
            predecessors = {rec.id: [p.id for p in rec[self.sched_predecessor_field_id.name] if p.id in ids]
                            for rec in records}
            cycle = scheduler.find_cycle(predecessors)
            if cycle:
                by_id = {rec.id: rec.display_name for rec in records}
                checks.append((BLOCKER, _("Circular dependency between: %s",
                                          ' → '.join(by_id[key] for key in cycle))))
            outside = records.filtered(lambda rec: rec[self.sched_predecessor_field_id.name].filtered(
                lambda p: p.id not in ids))
            if outside:
                checks.append((INFO, _("%(count)s record(s) depend on records outside the filter (ignored): %(names)s",
                                       count=len(outside), names=names(outside))))
        if self.sched_deadline_field_id:
            no_deadline = records.filtered(lambda rec: not rec[self.sched_deadline_field_id.name])
            if no_deadline and self.sched_objective == 'tardiness':
                checks.append((INFO, _("%(count)s record(s) have no deadline and never count as late: %(names)s",
                                       count=len(no_deadline), names=names(no_deadline))))
        if self.sched_time_mode == 'calendar':
            origin = self._or_sched_origin()
            total = sum(max(d, 0) for d in durations.values()) * factor
            try:
                axis = self._or_sched_work_axis(origin, origin, lambda axis: 0)
            except UserError as e:
                checks.append((BLOCKER, str(e.args[0])))
            else:
                if not axis.total:
                    checks.append((BLOCKER, _("The working hours %s have no working time.",
                                              self.sched_calendar_id.display_name)))
                else:
                    weekly = axis.total / max((axis.end - origin) / timedelta(days=7), 1)
                    checks.append((OK, _("Planning within %(calendar)s (about %(hours)s working hours a week); "
                                         "total work %(work)s hours.", calendar=self.sched_calendar_id.display_name,
                                         hours=round(weekly / 60), work=round(total / 60, 1))))
        return checks

    def _or_setup_checks_routing(self, records):
        _ = self.env._
        checks = []
        depot = self.route_depot_id
        if not self._or_has_location(depot.partner_latitude, depot.partner_longitude):
            checks.append((BLOCKER, _("The depot %s has no latitude/longitude.", depot.display_name)))
        vehicles = self.route_vehicle_ids.filtered('active')
        checks.append((OK, _("%s vehicle(s) available.", len(vehicles))))
        stops, missing, log = self._or_route_build_stops(records, self._or_route_departure())
        if missing:
            checks.append((BLOCKER, _("%(count)s stop(s) have no latitude/longitude: %(names)s",
                                      count=len(missing), names=names(missing))))
        checks += [(WARNING, message) for message in log]
        if self.route_demand_field_id:
            empty = [rec for rec in records if not self._or_num(rec, self.route_demand_field_id, 0.0)]
            if empty:
                checks.append((INFO, _("%(count)s stop(s) have no load: %(names)s",
                                       count=len(empty), names=names(empty))))
        log = []
        try:
            self._or_route_check_stops(stops, vehicles, {rec.id: rec.display_name for rec in records}, log)
        except UserError as e:
            checks.append((BLOCKER, str(e.args[0])))
        checks += [(WARNING, message) for message in log]
        return checks
