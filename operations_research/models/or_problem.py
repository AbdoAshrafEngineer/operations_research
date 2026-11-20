import hashlib
import json
import logging

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command, Domain
from odoo.tools.safe_eval import safe_eval, datetime as safe_datetime, dateutil as safe_dateutil, time as safe_time

from ..tools import linear
from .or_run import SOLVER_STATUS

_logger = logging.getLogger(__name__)

NUMERIC_TYPES = ('float', 'integer', 'monetary')
MATCH_TYPES = ('char', 'selection', 'many2one', 'integer', 'boolean')
MAX_CONFLICT_ANALYSIS_LINES = 20


def field_value_key(value):
    """Normalise a field value so two values can be compared (m2o by id)."""
    if isinstance(value, models.BaseModel):
        return value.id if len(value) <= 1 else tuple(sorted(value.ids))
    return value


class OrProblem(models.Model):
    _name = 'or.problem'
    _description = 'Optimization Problem'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'sequence, name, id'
    _check_company_auto = True

    name = fields.Char(required=True, translate=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Html(translate=True)
    company_id = fields.Many2one('res.company', required=True, index=True,
                                 default=lambda self: self.env.company)
    problem_type = fields.Selection(
        [('lp', 'Linear / Integer Program'), ('assignment', 'Assignment')],
        string='Problem Type', required=True, default='lp', tracking=True)

    # ------------------------------------------------------------------
    # Records the problem works on (any model, including custom addons)
    # ------------------------------------------------------------------
    model_id = fields.Many2one(
        'ir.model', string='Records Model', required=True, ondelete='cascade', tracking=True,
        domain=[('transient', '=', False), ('abstract', '=', False)])
    model_name = fields.Char(related='model_id.model', string='Model Technical Name', store=True)
    domain = fields.Char(string='Records Filter', default='[]',
                         help="Only records matching this filter take part in the problem.")
    record_count = fields.Integer(compute='_compute_record_count', string='Matching Records')

    # ------------------------------------------------------------------
    # LP / MIP: one decision variable per record
    # ------------------------------------------------------------------
    var_field_id = fields.Many2one(
        'ir.model.fields', string='Decision Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary', 'boolean'))]",
        help="The solver computes a value for this field on every record; Apply writes it back.")
    var_type = fields.Selection(
        [('continuous', 'Continuous'), ('integer', 'Integer'), ('binary', 'Yes / No (0-1)')],
        string='Variable Type', default='continuous', required=True)
    lb_value = fields.Float(string='Minimum', default=0.0)
    lb_field_id = fields.Many2one(
        'ir.model.fields', string='Minimum From Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    ub_infinite = fields.Boolean(string='No Maximum', default=True)
    ub_value = fields.Float(string='Maximum', default=0.0)
    ub_field_id = fields.Many2one(
        'ir.model.fields', string='Maximum From Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    objective_sense = fields.Selection(
        [('max', 'Maximize'), ('min', 'Minimize')], string='Goal', default='max', required=True)
    objective_field_id = fields.Many2one(
        'ir.model.fields', string='Objective Coefficient', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary'))]",
        help="Objective = sum over records of (this field x decision value). "
             "Leave empty to just look for any feasible solution.")
    objective_path = fields.Char(
        string='Objective Value', compute='_compute_objective_path', store=True, readonly=False,
        help="Value per unit to maximize or minimize. Can follow links, e.g. Product > Sales Price.")
    constraint_ids = fields.One2many('or.problem.constraint', 'problem_id', string='Constraints',
                                     copy=True)

    # ------------------------------------------------------------------
    # Assignment: each record (task) gets one resource (record of another model)
    # ------------------------------------------------------------------
    assign_field_id = fields.Many2one(
        'ir.model.fields', string='Assigned Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'many2one')]",
        help="Many2one field on the records that receives the chosen resource.")
    assign_model_name = fields.Char(related='assign_field_id.relation', string='Resource Model')
    assign_domain = fields.Char(string='Resources Filter', default='[]')
    assign_cost_mode = fields.Selection(
        [('constant', 'Same for every pair'),
         ('resource', 'Resource field'),
         ('product', 'Record field x resource field')],
        string='Cost', default='resource', required=True)
    assign_task_weight_field_id = fields.Many2one(
        'ir.model.fields', string='Record Weight', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    assign_resource_cost_field_id = fields.Many2one(
        'ir.model.fields', string='Resource Cost', ondelete='set null',
        domain="[('model', '=', assign_model_name), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    assign_task_load_field_id = fields.Many2one(
        'ir.model.fields', string='Record Load', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary'))]",
        help="Capacity consumed by each record. Empty = 1 per record.")
    assign_capacity_field_id = fields.Many2one(
        'ir.model.fields', string='Resource Capacity', ondelete='set null',
        domain="[('model', '=', assign_model_name), ('ttype', 'in', ('float', 'integer', 'monetary'))]",
        help="Empty = each resource takes at most one record.")
    assign_task_match_field_id = fields.Many2one(
        'ir.model.fields', string='Record Match Field', ondelete='set null',
        domain="[('model_id', '=', model_id), "
               "('ttype', 'in', ('char', 'selection', 'many2one', 'integer', 'boolean'))]")
    assign_resource_match_field_id = fields.Many2one(
        'ir.model.fields', string='Resource Match Field', ondelete='set null',
        domain="[('model', '=', assign_model_name), "
               "('ttype', 'in', ('char', 'selection', 'many2one', 'integer', 'boolean'))]")
    assign_mismatch_policy = fields.Selection(
        [('ignore', 'Ignore'), ('penalty', 'Add penalty'), ('forbid', 'Forbid')],
        string='When Not Matching', default='forbid', required=True)
    assign_mismatch_penalty = fields.Float(string='Mismatch Penalty', default=100.0)
    assign_all = fields.Boolean(string='Every Record Must Be Assigned', default=True)
    assign_unassigned_penalty = fields.Float(string='Unassigned Penalty', default=1000.0,
                                             help="Cost of leaving a record unassigned (when minimizing).")

    # ------------------------------------------------------------------
    # Solver parameters
    # ------------------------------------------------------------------
    time_limit = fields.Integer(string='Time Limit (s)',
                                default=lambda self: self._default_param('time_limit', 30))
    mip_gap = fields.Float(string='Accepted Gap (%)', default=0.0,
                           help="Stop integer search when the solution is within this % of the best bound.")
    num_workers = fields.Integer(string='Solver Threads',
                                 default=lambda self: self._default_param('num_workers', 1))

    # ------------------------------------------------------------------
    # Scenarios
    # ------------------------------------------------------------------
    parent_id = fields.Many2one('or.problem', string='Base Problem', index=True, ondelete='cascade',
                                check_company=True, copy=False)
    scenario_ids = fields.One2many('or.problem', 'parent_id', string='Scenarios',
                                   context={'active_test': False})
    scenario_count = fields.Integer(compute='_compute_scenario_count')
    is_scenario = fields.Boolean(compute='_compute_is_scenario', store=True)

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------
    schedule_active = fields.Boolean(string='Run Automatically', copy=False, tracking=True)
    schedule_interval_number = fields.Integer(string='Every', default=1)
    schedule_interval_type = fields.Selection(
        [('hours', 'Hours'), ('days', 'Days'), ('weeks', 'Weeks'), ('months', 'Months')],
        string='Interval Unit', default='days')
    schedule_next_date = fields.Datetime(string='Next Run', copy=False)
    schedule_user_id = fields.Many2one('res.users', string='Run As',
                                       default=lambda self: self.env.user)
    schedule_auto_apply = fields.Boolean(
        string='Apply Automatically', groups='operations_research.group_or_manager', copy=False,
        help="Apply results automatically when the solver proves optimality and no record "
             "changed since solving. The 'Run As' user must be an Optimization Manager.")

    run_ids = fields.One2many('or.run', 'problem_id', string='Runs')
    run_count = fields.Integer(compute='_compute_run_stats')
    last_run_id = fields.Many2one('or.run', compute='_compute_run_stats')
    last_objective = fields.Float(compute='_compute_run_stats', string='Last Objective')
    last_run_status = fields.Selection(SOLVER_STATUS, compute='_compute_run_stats', string='Last Status')

    _time_limit_positive = models.Constraint('CHECK(time_limit > 0)',
                                             'The time limit must be positive.')
    _mip_gap_range = models.Constraint('CHECK(mip_gap >= 0 AND mip_gap < 100)',
                                       'The accepted gap must be between 0 and 100%.')
    _num_workers_positive = models.Constraint('CHECK(num_workers > 0)',
                                              'The number of solver threads must be positive.')
    _schedule_interval_positive = models.Constraint(
        'CHECK(schedule_interval_number > 0)', 'The schedule interval must be positive.')

    # ------------------------------------------------------------------
    # Defaults / computes
    # ------------------------------------------------------------------
    @api.model
    def _default_param(self, key, default):
        value = self.env['ir.config_parameter'].sudo().get_param('operations_research.%s' % key)
        try:
            return int(value) if value else default
        except ValueError:
            return default

    @api.depends('model_name', 'domain')
    def _compute_record_count(self):
        for problem in self:
            try:
                problem.record_count = problem._or_get_records(raise_if_empty=False, count=True)
            except Exception:  # noqa: BLE001 - invalid domain while editing: show 0
                problem.record_count = 0

    @api.depends('objective_field_id')
    def _compute_objective_path(self):
        for problem in self:
            if problem.objective_field_id:
                problem.objective_path = problem.objective_field_id.name

    @api.depends('scenario_ids')
    def _compute_scenario_count(self):
        for problem in self:
            problem.scenario_count = len(problem.with_context(active_test=False).scenario_ids)

    @api.depends('parent_id')
    def _compute_is_scenario(self):
        for problem in self:
            problem.is_scenario = bool(problem.parent_id)

    def _compute_run_stats(self):
        data = self.env['or.run']._read_group(
            [('problem_id', 'in', self.ids)], ['problem_id'], ['__count', 'id:max'])
        stats = {problem.id: (count, last_id) for problem, count, last_id in data}
        for problem in self:
            count, last_id = stats.get(problem.id, (0, False))
            last_run = self.env['or.run'].browse(last_id)
            problem.run_count = count
            problem.last_run_id = last_run
            problem.last_objective = last_run.objective_value
            problem.last_run_status = last_run.solver_status

    @api.ondelete(at_uninstall=False)
    def _unlink_except_running(self):
        if self.env['or.run'].sudo().search_count(
                [('problem_id', 'in', self.ids), ('state', '=', 'running')], limit=1):
            raise UserError(self.env._("You cannot delete a problem while one of its runs is solving."))

    # ------------------------------------------------------------------
    # Onchanges / constraints
    # ------------------------------------------------------------------
    @api.onchange('model_id')
    def _onchange_model_id(self):
        for problem in self:
            for fname in problem._or_model_field_names():
                if problem[fname] and problem[fname].model_id != problem.model_id:
                    problem[fname] = False
            problem.constraint_ids = [Command.clear()]
            problem.domain = '[]'

    @api.onchange('var_field_id')
    def _onchange_var_field_id(self):
        for problem in self:
            if problem.var_field_id.ttype == 'boolean':
                problem.var_type = 'binary'
            elif problem.var_field_id.ttype == 'integer' and problem.var_type == 'continuous':
                problem.var_type = 'integer'

    def _or_model_field_names(self):
        """Many2one(ir.model.fields) whose field must belong to ``model_id``."""
        return ['var_field_id', 'lb_field_id', 'ub_field_id', 'objective_field_id',
                'assign_field_id', 'assign_task_weight_field_id', 'assign_task_load_field_id',
                'assign_task_match_field_id']

    @api.constrains('parent_id', 'problem_type')
    def _check_parent_id(self):
        for problem in self:
            if problem.parent_id.parent_id or (problem.parent_id and problem.scenario_ids):
                raise ValidationError(self.env._("A scenario cannot be based on another scenario."))
            family = problem.parent_id | problem.with_context(active_test=False).scenario_ids
            if any(other.problem_type != problem.problem_type for other in family):
                raise ValidationError(self.env._("A scenario must keep the problem type of its base problem."))

    @api.constrains('model_id', 'var_field_id', 'lb_field_id', 'ub_field_id', 'objective_field_id',
                    'assign_field_id', 'assign_task_weight_field_id', 'assign_task_load_field_id',
                    'assign_task_match_field_id', 'var_type')
    def _check_fields_model(self):
        for problem in self:
            for fname in problem._or_model_field_names():
                field = problem[fname]
                if field and field.model_id != problem.model_id:
                    raise ValidationError(self.env._(
                        "Field %(field)s does not belong to model %(model)s.",
                        field=field.field_description, model=problem.model_id.name))
            if problem.var_field_id:
                problem._check_writable_field(problem.var_field_id)
                if problem.var_field_id.ttype == 'boolean' and problem.var_type != 'binary':
                    raise ValidationError(self.env._(
                        "A Yes/No decision field requires the variable type 'Yes / No (0-1)'."))
            if problem.assign_field_id:
                problem._check_writable_field(problem.assign_field_id)

    @api.constrains('assign_field_id', 'assign_resource_cost_field_id', 'assign_capacity_field_id',
                    'assign_resource_match_field_id')
    def _check_assign_resource_fields(self):
        for problem in self:
            relation = problem.assign_field_id.relation
            for fname in ('assign_resource_cost_field_id', 'assign_capacity_field_id',
                          'assign_resource_match_field_id'):
                field = problem[fname]
                if field and field.model != relation:
                    raise ValidationError(self.env._(
                        "Field %(field)s must belong to the resource model %(model)s.",
                        field=field.field_description, model=relation or '-'))

    @api.constrains('objective_path', 'model_id')
    def _check_objective_path(self):
        for problem in self:
            if problem.objective_path and problem.model_name in self.env:
                problem._or_check_path(problem.model_name, problem.objective_path)

    def _or_check_path(self, model_name, path):
        """A value path must follow many2one links and end on a number."""
        Model = self.env[model_name]
        parts = path.split('.')
        for i, part in enumerate(parts):
            field = Model._fields.get(part)
            if field is None:
                raise ValidationError(self.env._("Unknown field %(field)s in %(path)s.", field=part, path=path))
            if i < len(parts) - 1:
                if field.type != 'many2one':
                    raise ValidationError(self.env._(
                        "%(path)s: only single links (many2one) can be followed.", path=path))
                Model = self.env[field.comodel_name]
            elif field.type not in NUMERIC_TYPES:
                raise ValidationError(self.env._("%(path)s must end on a number field.", path=path))

    @api.model
    def _or_path_num(self, record, path, default=0.0):
        """Numeric value at ``path`` (e.g. 'product_id.standard_price') on ``record``."""
        if not path:
            return default
        value = record
        for part in path.split('.'):
            value = value[part]
            if isinstance(value, models.BaseModel) and not value:
                return default
        return float(value) if value not in (False, None) else default

    @api.constrains('domain', 'assign_domain')
    def _check_domains(self):
        for problem in self:
            for fname in ('domain', 'assign_domain'):
                try:
                    domain = problem._or_eval_domain(problem[fname])
                    Domain(domain)
                except Exception as e:  # noqa: BLE001
                    raise ValidationError(self.env._("Invalid filter: %(error)s", error=e)) from e

    def _check_writable_field(self, ir_field):
        model = self.env.get(ir_field.model)
        field = model._fields.get(ir_field.name) if model is not None else None
        if field is None:
            raise ValidationError(self.env._("Field %s no longer exists.", ir_field.name))
        writable = (field.store or field.inverse) and not (field.compute and not field.inverse and field.readonly)
        if not writable or field.related:
            raise ValidationError(self.env._(
                "Field %(field)s cannot be written (computed, related or not stored); "
                "choose a stored editable field.", field=ir_field.field_description))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _or_eval_context(self):
        return {
            'uid': self.env.uid,
            'user': self.env.user,
            'company_id': self.env.company.id,
            'company_ids': self.env.companies.ids,
            'datetime': safe_datetime,
            'dateutil': safe_dateutil,
            'time': safe_time,
            'context_today': lambda: fields.Date.context_today(self),
            'relativedelta': relativedelta,
        }

    def _or_eval_domain(self, domain_str):
        if not domain_str or not domain_str.strip():
            return []
        domain = safe_eval(domain_str, self._or_eval_context())
        if not isinstance(domain, (list, tuple)):
            raise ValueError(self.env._("A filter must be a list of conditions."))
        return list(domain)

    def _or_get_model(self, model_name=None):
        model_name = model_name or self.model_name
        if not model_name or model_name not in self.env:
            raise UserError(self.env._("Model %s is not installed.", model_name or '-'))
        return self.env[model_name]

    def _or_get_records(self, raise_if_empty=True, count=False):
        """Records of the problem, read with the current user's access rights."""
        self.ensure_one()
        Model = self._or_get_model()
        try:
            domain = Domain(self._or_eval_domain(self.domain))
            if count:
                return Model.search_count(domain)
            records = Model.search(domain, order='id')
        except (ValueError, KeyError, TypeError, SyntaxError, NameError) as e:
            raise UserError(self.env._("Invalid records filter: %s", e)) from e
        if raise_if_empty and not records:
            raise UserError(self.env._("No %(model)s record matches the problem filter.",
                                       model=self.model_id.name))
        return records

    def _or_get_resources(self):
        Model = self._or_get_model(self.assign_model_name)
        try:
            domain = Domain(self._or_eval_domain(self.assign_domain))
        except Exception as e:  # noqa: BLE001
            raise UserError(self.env._("Invalid resources filter: %s", e)) from e
        try:
            resources = Model.search(domain, order='id')
        except (ValueError, KeyError, TypeError) as e:
            raise UserError(self.env._("Invalid resources filter: %s", e)) from e
        if not resources:
            raise UserError(self.env._("No resource matches the resources filter."))
        return resources

    @api.model
    def _or_num(self, record, ir_field, default=0.0):
        """Numeric value of ``ir_field`` on ``record`` (False/None -> default)."""
        if not ir_field:
            return default
        value = record[ir_field.name]
        return float(value) if value not in (False, None) else default

    def _or_max_variables(self):
        return self._default_param('max_variables', 200000)

    def _or_sync_max_variables(self):
        return self._default_param('sync_max_variables', 2000)

    def _or_estimate_size(self):
        """Approximate number of solver variables, used to choose sync vs queued."""
        self.ensure_one()
        method = getattr(self, '_or_estimate_size_%s' % self.problem_type, None)
        count = self._or_get_records(raise_if_empty=False, count=True)
        if not count:
            raise UserError(self.env._("No %(model)s record matches the problem filter.",
                                       model=self.model_id.name))
        return method(count) if method else count

    def _or_estimate_size_assignment(self, count):
        return count * len(self._or_get_resources())

    def _or_check_size(self, size):
        limit = self._or_max_variables()
        if size > limit:
            raise UserError(self.env._(
                "This problem has about %(size)s variables, above the limit of %(limit)s. "
                "Narrow the records filter or raise the limit in Settings.",
                size=size, limit=limit))

    def _or_config_signature(self):
        """Hash of the configuration that decides where results are written."""
        self.ensure_one()
        values = {fname: self[fname].id for fname in self._or_signature_fields()}
        values['model'] = self.model_name
        return hashlib.sha1(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def _or_signature_fields(self):
        return ['var_field_id', 'assign_field_id']

    def _or_solver_kwargs(self):
        return {
            'time_limit': self.time_limit or 30,
            'num_threads': self.num_workers or 1,
        }

    # ------------------------------------------------------------------
    # Dispatch: templates implement _or_check_<type>, _or_solve_<type>,
    # _or_apply_<type> and _or_snapshot_<type>.
    # ------------------------------------------------------------------
    def _or_dispatch(self, prefix, *args):
        method = getattr(self, '%s_%s' % (prefix, self.problem_type), None)
        if method is None:
            raise UserError(self.env._("Problem type %s is not supported.", self.problem_type))
        return method(*args)

    def _or_check_config(self):
        self.ensure_one()
        return self._or_dispatch('_or_check')

    def _or_solve(self, run):
        self.ensure_one()
        self._or_check_config()
        return self._or_dispatch('_or_solve', run)

    def _or_apply(self, run, lines):
        self.ensure_one()
        return self._or_dispatch('_or_apply', run, lines)

    def _or_snapshot(self, records):
        """Current values of the written fields: {record_id: str} for conflict detection."""
        self.ensure_one()
        return self._or_dispatch('_or_snapshot', records)

    # ------------------------------------------------------------------
    # LP / MIP template
    # ------------------------------------------------------------------
    def _or_check_lp(self):
        if not self.var_field_id:
            raise UserError(self.env._("Choose the decision field of problem %s.", self.name))
        self._check_writable_field(self.var_field_id)
        for line in self.constraint_ids.filtered('active'):
            line._or_check_config()

    def _or_lp_bounds(self, record):
        if self.var_type == 'binary':
            return 0.0, 1.0
        lb = self._or_num(record, self.lb_field_id, self.lb_value) if self.lb_field_id else self.lb_value
        if self.ub_infinite and not self.ub_field_id:
            ub = None
        elif self.ub_field_id:
            ub = self._or_num(record, self.ub_field_id, None)
        else:
            ub = self.ub_value
        return lb, ub

    def _or_lp_build(self, records):
        variables, objective, bad_bounds = [], {}, []
        for record in records:
            lb, ub = self._or_lp_bounds(record)
            if ub is not None and lb is not None and lb > ub + linear.EPS:
                bad_bounds.append(record.display_name)
            variables.append({'key': record.id, 'lb': lb, 'ub': ub, 'type': self.var_type})
            objective[record.id] = self._or_path_num(record, self.objective_path, 0.0)
        if bad_bounds:
            raise UserError(self.env._(
                "Minimum is greater than maximum for: %s", ', '.join(bad_bounds[:10])))
        constraints, rows_meta, log = [], {}, []
        for line in self.constraint_ids.filtered('active').sorted('sequence'):
            for row in line._or_build_rows(records, log):
                constraints.append(row)
                rows_meta[row['key']] = row
        return variables, objective, constraints, rows_meta, log

    def _or_solve_lp(self, run):
        records = self._or_get_records()
        self._or_check_size(len(records))
        variables, objective, constraints, rows_meta, log = self._or_lp_build(records)
        kwargs = self._or_solver_kwargs()
        if self.mip_gap:
            kwargs['mip_gap'] = self.mip_gap / 100.0
        result = linear.solve_linear(variables, objective, constraints,
                                     maximize=self.objective_sense == 'max', **kwargs)
        fname = self.var_field_id.name
        snapshot = self._or_snapshot_lp(records)
        lines = []
        if result['status'] in linear.SOLUTION_STATUSES:
            for record in records:
                value = result['values'][record.id]
                lines.append({
                    'res_model': self.model_name,
                    'res_id': record.id,
                    'res_name': record.display_name,
                    'value_before': float(record[fname] or 0.0),
                    'value': value,
                    'reduced_cost': result['reduced_costs'].get(record.id, 0.0),
                    'objective_coef': objective[record.id],
                    'snapshot': snapshot[record.id],
                })
        constraint_lines = []
        for key, info in result['constraints'].items():
            meta = rows_meta[key]
            constraint_lines.append({
                'constraint_id': meta['line_id'],
                'name': meta['name'],
                'operator': meta['op'],
                'rhs': meta['rhs'],
                'activity': info['activity'],
                'slack': info['slack'],
                'dual_value': info['dual'],
            })
        conflicts = self.env['or.problem.constraint']
        if result['status'] == linear.INFEASIBLE:
            conflicts, analysis_log = self._or_lp_explain_infeasibility(
                variables, objective, constraints, rows_meta)
            log += analysis_log
        result.update({
            'lines': lines,
            'constraint_lines': constraint_lines,
            'conflict_ids': conflicts.ids,
            'log': log,
        })
        return result

    def _or_lp_explain_infeasibility(self, variables, objective, constraints, rows_meta):
        """Find constraint lines whose removal alone restores feasibility."""
        line_ids = list(dict.fromkeys(meta['line_id'] for meta in rows_meta.values()))
        log = []
        conflicts = self.env['or.problem.constraint']
        if not line_ids:
            log.append(self.env._("Infeasible because of the variable bounds (minimum/maximum)."))
            return conflicts, log
        if len(line_ids) > MAX_CONFLICT_ANALYSIS_LINES:
            log.append(self.env._("Too many constraints to analyse infeasibility automatically."))
            return conflicts, log
        kwargs = self._or_solver_kwargs()
        kwargs['time_limit'] = min(kwargs['time_limit'], 10)
        for line_id in line_ids:
            subset = [c for c in constraints if rows_meta[c['key']]['line_id'] != line_id]
            result = linear.solve_linear(variables, {}, subset, **kwargs)
            if result['status'] in linear.SOLUTION_STATUSES:
                conflicts |= conflicts.browse(line_id)
        if conflicts:
            log.append(self.env._("Removing any one of these constraints makes the problem feasible: %s",
                                  ', '.join(conflicts.mapped('name'))))
        else:
            # try removing all constraints: if still infeasible, bounds are the culprit
            result = linear.solve_linear(variables, {}, [], **kwargs)
            if result['status'] not in linear.SOLUTION_STATUSES:
                log.append(self.env._("Infeasible because of the variable bounds (minimum/maximum)."))
            else:
                log.append(self.env._("Several constraints conflict together; "
                                      "no single constraint explains it."))
        return conflicts, log

    def _or_snapshot_lp(self, records):
        fname = self.var_field_id.name
        return {rec.id: repr(round(float(rec[fname] or 0.0), 6)) for rec in records}

    def _or_apply_lp(self, run, lines):
        field = self.var_field_id
        Model = self._or_get_model()
        groups = {}
        for line in lines:
            if field.ttype == 'boolean':
                value = line.value > 0.5
            elif field.ttype == 'integer':
                value = int(round(line.value))
            else:
                value = line.value
            groups.setdefault(value, []).append(line.res_id)
        for value, ids in groups.items():
            Model.browse(ids).write({field.name: value})

    # ------------------------------------------------------------------
    # Assignment template
    # ------------------------------------------------------------------
    def _or_check_assignment(self):
        if not self.assign_field_id:
            raise UserError(self.env._("Choose the assigned field of problem %s.", self.name))
        self._check_writable_field(self.assign_field_id)
        if self.assign_cost_mode in ('resource', 'product') and not self.assign_resource_cost_field_id:
            raise UserError(self.env._("Choose the resource cost field."))
        if self.assign_cost_mode == 'product' and not self.assign_task_weight_field_id:
            raise UserError(self.env._("Choose the record weight field."))
        if bool(self.assign_task_match_field_id) != bool(self.assign_resource_match_field_id):
            raise UserError(self.env._("Choose both match fields, or none."))

    def _or_assignment_cost(self, task, resource):
        if self.assign_cost_mode == 'constant':
            return 1.0
        cost = self._or_num(resource, self.assign_resource_cost_field_id)
        if self.assign_cost_mode == 'product':
            cost *= self._or_num(task, self.assign_task_weight_field_id)
        return cost

    def _or_solve_assignment(self, run):
        tasks = self._or_get_records()
        resources = self._or_get_resources()
        self._or_check_size(len(tasks) * len(resources))
        maximize = self.objective_sense == 'max'
        costs, allowed = {}, set()
        match = self.assign_task_match_field_id and self.assign_resource_match_field_id
        for task in tasks:
            task_key = field_value_key(task[self.assign_task_match_field_id.name]) if match else None
            for resource in resources:
                cost = self._or_assignment_cost(task, resource)
                if match:
                    res_key = field_value_key(resource[self.assign_resource_match_field_id.name])
                    if task_key != res_key:
                        if self.assign_mismatch_policy == 'forbid':
                            continue
                        if self.assign_mismatch_policy == 'penalty':
                            cost += -self.assign_mismatch_penalty if maximize else self.assign_mismatch_penalty
                costs[(task.id, resource.id)] = cost
                allowed.add((task.id, resource.id))
        loads = {task.id: self._or_num(task, self.assign_task_load_field_id, 1.0)
                 if self.assign_task_load_field_id else 1.0 for task in tasks}
        capacities = {res.id: self._or_num(res, self.assign_capacity_field_id, 0.0)
                      if self.assign_capacity_field_id else 1.0 for res in resources}
        log = []
        orphans = [task.display_name for task in tasks
                   if not any((task.id, res.id) in allowed for res in resources)]
        if orphans:
            log.append(self.env._("No matching resource for: %s", ', '.join(orphans[:20])))
        result = linear.solve_assignment(
            tasks.ids, resources.ids, costs, loads, capacities, allowed=allowed,
            assign_all=self.assign_all, unassigned_penalty=self.assign_unassigned_penalty,
            maximize=maximize, **self._or_solver_kwargs())
        fname = self.assign_field_id.name
        snapshot = self._or_snapshot_assignment(tasks)
        res_by_id = {res.id: res for res in resources}
        lines = []
        if result['status'] in linear.SOLUTION_STATUSES:
            for task in tasks:
                res_id = result['assignment'][task.id]
                lines.append({
                    'res_model': self.model_name,
                    'res_id': task.id,
                    'res_name': task.display_name,
                    'assigned_res_id': res_id or 0,
                    'assigned_name': res_by_id[res_id].display_name if res_id else False,
                    'assigned_before_name': task[fname].display_name or False,
                    'value_before': float(task[fname].id or 0),
                    'value': float(res_id or 0),
                    'objective_coef': costs.get((task.id, res_id), 0.0) if res_id else 0.0,
                    'snapshot': snapshot[task.id],
                })
        constraint_lines = []
        for res_id, info in result['capacity_usage'].items():
            constraint_lines.append({
                'name': self.env._("Capacity of %s", res_by_id[res_id].display_name),
                'operator': '<=',
                'rhs': capacities[res_id],
                'activity': info['activity'],
                'slack': info['slack'],
                'dual_value': info['dual'],
            })
        if result['status'] == linear.INFEASIBLE:
            total_load = sum(loads.values())
            total_cap = sum(capacities.values())
            log.append(self.env._(
                "Not every record can be assigned: total load %(load)s, total capacity %(cap)s. "
                "Add resources, raise capacities or untick 'Every Record Must Be Assigned'.",
                load=round(total_load, 2), cap=round(total_cap, 2)))
        unassigned = [line['res_name'] for line in lines if not line['assigned_res_id']]
        if unassigned:
            log.append(self.env._("Left unassigned: %s", ', '.join(unassigned[:20])))
        result.update({'lines': lines, 'constraint_lines': constraint_lines, 'log': log})
        return result

    def _or_snapshot_assignment(self, records):
        fname = self.assign_field_id.name
        return {rec.id: str(rec[fname].id or 0) for rec in records}

    def _or_apply_assignment(self, run, lines):
        Model = self._or_get_model()
        field = Model._fields[self.assign_field_id.name]
        groups = {}
        for line in lines:
            if not line.assigned_res_id and field.required:
                continue
            groups.setdefault(line.assigned_res_id or False, []).append(line.res_id)
        for res_id, ids in groups.items():
            Model.browse(ids).write({field.name: res_id})

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _or_new_run(self, trigger='manual', user=None):
        self.ensure_one()
        return self.env['or.run'].create({
            'problem_id': self.id,
            'trigger': trigger,
            'user_id': (user or self.env.user).id,
            'company_id': self.company_id.id,
        })

    def action_run(self):
        """Solve now when small, otherwise queue in the background."""
        self.ensure_one()
        self._or_check_config()
        size = self._or_estimate_size()
        self._or_check_size(size)
        run = self._or_new_run()
        if size <= self._or_sync_max_variables():
            run._solve()
        else:
            run.action_queue()
        return run._get_form_action()

    def action_queue(self):
        self.ensure_one()
        self._or_check_config()
        run = self._or_new_run()
        run.action_queue()
        return run._get_form_action()

    def action_create_scenario(self):
        self.ensure_one()
        base = self.parent_id or self
        scenario = base.copy({
            'parent_id': base.id,
            'name': self.env._("%(name)s - Scenario %(number)s",
                               name=base.name, number=base.scenario_count + 1),
            'schedule_active': False,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'or.problem',
            'res_id': scenario.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_view_runs(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('operations_research.or_run_action')
        action['domain'] = [('problem_id', '=', self.id)]
        action['context'] = {'default_problem_id': self.id}
        return action

    def action_view_scenarios(self):
        self.ensure_one()
        base = self.parent_id or self
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Scenarios"),
            'res_model': 'or.problem',
            'view_mode': 'list,form',
            'domain': ['|', ('id', '=', base.id), ('parent_id', '=', base.id)],
            'context': {'active_test': False},
        }

    def action_compare_scenarios(self):
        self.ensure_one()
        base = self.parent_id or self
        action = self.env['ir.actions.act_window']._for_xml_id('operations_research.or_run_action')
        action.update({
            'name': self.env._("Scenario Comparison"),
            'domain': ['|', ('problem_id', '=', base.id), ('problem_id.parent_id', '=', base.id)],
            'context': {'search_default_group_problem': 1},
            'view_mode': 'list,graph,form',
        })
        return action

    def action_view_records(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.model_id.name,
            'res_model': self.model_name,
            'view_mode': 'list,form',
            'domain': self._or_eval_domain(self.domain),
        }

    # ------------------------------------------------------------------
    # Scheduled runs
    # ------------------------------------------------------------------
    def _or_next_date(self, from_date):
        delta = {
            'hours': relativedelta(hours=self.schedule_interval_number),
            'days': relativedelta(days=self.schedule_interval_number),
            'weeks': relativedelta(weeks=self.schedule_interval_number),
            'months': relativedelta(months=self.schedule_interval_number),
        }[self.schedule_interval_type or 'days']
        return from_date + delta

    @api.onchange('schedule_active')
    def _onchange_schedule_active(self):
        for problem in self:
            if problem.schedule_active and not problem.schedule_next_date:
                problem.schedule_next_date = fields.Datetime.now()

    @api.model
    def _cron_run_scheduled(self):
        now = fields.Datetime.now()
        problems = self.search([
            ('schedule_active', '=', True),
            ('schedule_next_date', '<=', now),
        ])
        runs = self.env['or.run']
        for problem in problems:
            user = problem.schedule_user_id or problem.create_uid
            next_date = problem.schedule_next_date or now
            while next_date <= now:
                next_date = problem._or_next_date(next_date)
            problem.schedule_next_date = next_date
            run = problem._or_new_run(trigger='scheduled', user=user)
            run.auto_apply = problem.sudo().schedule_auto_apply
            runs |= run
        if runs:
            runs.action_queue()
        return runs
