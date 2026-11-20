import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_list

from ..tools import linear

_logger = logging.getLogger(__name__)

SOLVER_STATUS = [
    ('optimal', 'Optimal'),
    ('feasible', 'Feasible (not proven optimal)'),
    ('infeasible', 'Infeasible'),
    ('unbounded', 'Unbounded'),
    ('not_solved', 'No solution in time limit'),
    ('abnormal', 'Solver error'),
    ('model_invalid', 'Invalid model'),
]


class OrRun(models.Model):
    _name = 'or.run'
    _description = 'Optimization Run'
    _inherit = ['mail.thread']
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True, copy=False, default='/')
    problem_id = fields.Many2one('or.problem', required=True, ondelete='cascade', index=True,
                                 check_company=True, readonly=True)
    problem_type = fields.Selection(related='problem_id.problem_type', store=True)
    base_problem_id = fields.Many2one('or.problem', compute='_compute_base_problem', store=True,
                                      string='Base Problem', index=True)
    company_id = fields.Many2one('res.company', required=True, index=True, readonly=True,
                                 default=lambda self: self.env.company)
    user_id = fields.Many2one('res.users', string='Run As', required=True, readonly=True,
                              default=lambda self: self.env.user)
    trigger = fields.Selection([('manual', 'Manual'), ('scheduled', 'Scheduled')],
                               default='manual', required=True, readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('queued', 'Queued'),
        ('running', 'Running'),
        ('done', 'Solved'),
        ('failed', 'Failed'),
        ('applied', 'Applied'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, readonly=True, tracking=True, index=True, copy=False)
    solver_status = fields.Selection(SOLVER_STATUS, readonly=True, tracking=True, copy=False)
    has_solution = fields.Boolean(compute='_compute_has_solution', store=True)
    solver_name = fields.Char(readonly=True)
    objective_value = fields.Float(string='Objective', readonly=True, aggregator='avg')
    best_bound = fields.Float(readonly=True)
    gap = fields.Float(string='Gap (%)', readonly=True, aggregator='max')
    solve_time = fields.Float(string='Solve Time (s)', readonly=True, aggregator='avg')
    num_variables = fields.Integer(readonly=True, aggregator='max')
    num_constraints = fields.Integer(readonly=True, aggregator='max')
    has_sensitivity = fields.Boolean(readonly=True)
    log = fields.Text(readonly=True)
    error_message = fields.Text(readonly=True)
    config_signature = fields.Char(readonly=True, copy=False)
    auto_apply = fields.Boolean(readonly=True)
    date_queued = fields.Datetime(readonly=True)
    date_started = fields.Datetime(readonly=True)
    date_done = fields.Datetime(readonly=True)
    date_applied = fields.Datetime(readonly=True)
    applied_by_id = fields.Many2one('res.users', readonly=True)
    line_ids = fields.One2many('or.run.line', 'run_id', string='Results', readonly=True)
    line_count = fields.Integer(compute='_compute_line_counts')
    changed_count = fields.Integer(compute='_compute_line_counts', string='Changed Records')
    constraint_result_ids = fields.One2many('or.run.constraint', 'run_id', string='Constraint Results',
                                            readonly=True)
    conflict_ids = fields.Many2many('or.problem.constraint', string='Conflicting Constraints',
                                    readonly=True)

    @api.depends('problem_id.parent_id')
    def _compute_base_problem(self):
        for run in self:
            run.base_problem_id = run.problem_id.parent_id or run.problem_id

    @api.depends('solver_status')
    def _compute_has_solution(self):
        for run in self:
            run.has_solution = run.solver_status in linear.SOLUTION_STATUSES

    def _compute_line_counts(self):
        data = self.env['or.run.line']._read_group(
            [('run_id', 'in', self.ids)], ['run_id', 'changed'], ['__count'])
        counts = {}
        for run, changed, count in data:
            total, changed_total = counts.get(run.id, (0, 0))
            counts[run.id] = (total + count, changed_total + (count if changed else 0))
        for run in self:
            run.line_count, run.changed_count = counts.get(run.id, (0, 0))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code('or.run') or '/'
        return super().create(vals_list)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_running(self):
        if any(run.state == 'running' for run in self):
            raise UserError(self.env._("You cannot delete a run while it is solving."))

    def _get_form_action(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'or.run',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # Queue
    # ------------------------------------------------------------------
    def action_queue(self):
        for run in self:
            if run.state not in ('draft', 'failed', 'cancelled'):
                raise UserError(self.env._("Run %s cannot be queued in its current state.", run.name))
        self.write({'state': 'queued', 'date_queued': fields.Datetime.now(), 'error_message': False})
        self.env.ref('operations_research.ir_cron_or_process_queue')._trigger()
        return True

    def action_cancel(self):
        for run in self:
            if run.state not in ('draft', 'queued', 'done', 'failed'):
                raise UserError(self.env._("Run %s cannot be cancelled in its current state.", run.name))
        self.write({'state': 'cancelled'})
        return True

    def action_solve_now(self):
        for run in self:
            if run.state not in ('draft', 'queued', 'failed', 'cancelled'):
                raise UserError(self.env._("Run %s cannot be solved in its current state.", run.name))
        self._solve()
        return True

    @api.model
    def _stuck_delay(self):
        minutes = self.env['or.problem']._default_param('stuck_minutes', 60)
        return timedelta(minutes=minutes)

    @api.model
    def _fail_stuck_runs(self):
        limit = fields.Datetime.now() - self._stuck_delay()
        stuck = self.sudo().search([('state', '=', 'running'), ('date_started', '<', limit)])
        for run in stuck:
            run.write({
                'state': 'failed',
                'error_message': self.env._("The solver was interrupted (server restart or worker "
                                            "timeout). Queue it again or reduce the time limit."),
            })
        return stuck

    @api.model
    def _cron_process_queue(self, limit=20):
        self._fail_stuck_runs()
        runs = self.sudo().search([('state', '=', 'queued')], order='date_queued, id', limit=limit)
        in_cron = bool(self.env.context.get('cron_id'))
        remaining = len(runs)
        for run in runs:
            if in_cron:
                # make "running" visible to users and protect against parallel workers
                run.state = 'running'
                run.date_started = fields.Datetime.now()
                self.env['ir.cron']._commit_progress(remaining=remaining)
            run._solve()
            remaining -= 1
            if in_cron:
                time_left = self.env['ir.cron']._commit_progress(1, remaining=remaining)
                if time_left <= 0:
                    break
        if self.sudo().search_count([('state', '=', 'queued')], limit=1):
            self.env.ref('operations_research.ir_cron_or_process_queue')._trigger()
        return runs

    # ------------------------------------------------------------------
    # Solve
    # ------------------------------------------------------------------
    def _solve(self):
        """Solve each run as its own user (record rules of that user apply)."""
        for run in self:
            run_sudo = run.sudo()
            run_sudo.line_ids.unlink()
            run_sudo.constraint_result_ids.unlink()
            run_sudo.write({
                'state': 'running',
                'date_started': fields.Datetime.now(),
                'error_message': False,
                'log': False,
                'solver_status': False,
                'conflict_ids': [fields.Command.clear()],
            })
            problem = run.problem_id.with_user(run.user_id).with_context(
                lang=run.user_id.lang or self.env.lang).with_company(run.company_id)
            try:
                with self.env.cr.savepoint():
                    result = problem._or_solve(run)
            except (UserError, ValidationError, AccessError) as e:
                run_sudo.write({'state': 'failed', 'error_message': str(e.args[0] if e.args else e),
                                'date_done': fields.Datetime.now()})
            except Exception as e:  # noqa: BLE001 - never leave a run "running"
                _logger.exception("Optimization run %s failed", run.name)
                run_sudo.write({'state': 'failed',
                                'error_message': self.env._("Unexpected error: %s", e),
                                'date_done': fields.Datetime.now()})
            else:
                run_sudo._store_result(result, problem)
            run._notify_finished()
        return True

    def _store_result(self, result, problem):
        self.ensure_one()
        self.write({
            'state': 'done',
            'solver_status': result['status'],
            'solver_name': result.get('solver'),
            'objective_value': result.get('objective', 0.0),
            'best_bound': result.get('bound', 0.0),
            'gap': (result.get('gap') or 0.0) * 100.0,
            'solve_time': result.get('wall_time', 0.0),
            'num_variables': result.get('num_variables', 0),
            'num_constraints': result.get('num_constraints', 0),
            'has_sensitivity': result.get('has_sensitivity', False),
            'log': '\n'.join(str(entry) for entry in result.get('log', [])) or False,
            'date_done': fields.Datetime.now(),
            'config_signature': problem._or_config_signature(),
            'conflict_ids': [fields.Command.set(result.get('conflict_ids', []))],
        })
        self.env['or.run.line'].create([dict(vals, run_id=self.id) for vals in result.get('lines', [])])
        self.env['or.run.constraint'].create([
            dict(vals, run_id=self.id) for vals in result.get('constraint_lines', [])])

    def _notify_finished(self):
        for run in self.filtered(lambda r: r.trigger == 'scheduled' or r.date_queued):
            status = dict(SOLVER_STATUS).get(run.solver_status) or dict(
                self._fields['state']._description_selection(self.env)).get(run.state)
            body = Markup("<p>%s</p>") % self.env._(
                "Optimization %(problem)s finished: %(status)s.",
                problem=run.problem_id.name, status=status)
            run.sudo().message_post(
                body=body,
                partner_ids=run.user_id.partner_id.ids,
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
                author_id=self.env.ref('base.partner_root').id,
            )
            if run.auto_apply and run.solver_status == 'optimal':
                run._auto_apply()

    def _auto_apply(self):
        self.ensure_one()
        user = self.user_id
        if not user.has_group('operations_research.group_or_manager'):
            self.sudo().message_post(body=self.env._(
                "Not applied automatically: %s is not an Optimization Manager.", user.name))
            return
        try:
            with self.env.cr.savepoint():
                self.with_user(user).action_apply()
        except (UserError, AccessError, ValidationError) as e:
            self.sudo().message_post(body=self.env._("Automatic apply failed: %s", e))

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------
    def _check_can_apply(self):
        if not self.env.user.has_group('operations_research.group_or_manager'):
            raise AccessError(self.env._("Only Optimization Managers can apply results."))
        for run in self:
            if run.state != 'done':
                raise UserError(self.env._("Run %s has no result to apply (state: %s).",
                                           run.name, run.state))
            if not run.has_solution:
                raise UserError(self.env._("Run %s has no feasible solution to apply.", run.name))
            if run.config_signature != run.problem_id._or_config_signature():
                raise UserError(self.env._(
                    "The problem configuration changed since run %s was solved; run it again.",
                    run.name))

    def _get_conflicts(self):
        """Lines whose record was deleted or changed since solving."""
        self.ensure_one()
        problem = self.problem_id
        Model = problem._or_get_model()
        lines = self.line_ids
        existing = Model.browse(lines.mapped('res_id')).exists()
        missing = lines.filtered(lambda line: line.res_id not in existing.ids)
        current = problem._or_snapshot(existing)
        changed = (lines - missing).filtered(lambda line: current.get(line.res_id) != line.snapshot)
        return missing, changed

    def action_apply(self, force=False):
        self._check_can_apply()
        for run in self:
            missing, changed = run._get_conflicts()
            if (missing or changed) and not force:
                names = (missing | changed)[:10].mapped('res_name')
                raise UserError(self.env._(
                    "%(count)s record(s) were changed or deleted since this run was solved: %(names)s. "
                    "Run the problem again, or use Force Apply to overwrite.",
                    count=len(missing | changed), names=format_list(self.env, names)))
            lines = run.line_ids - missing
            run.problem_id._or_apply(run, lines)
            run.write({'state': 'applied', 'date_applied': fields.Datetime.now(),
                       'applied_by_id': self.env.user.id})
            body = self.env._("Results applied to %(count)s record(s).", count=len(lines))
            if missing:
                body += ' ' + self.env._("%s deleted record(s) skipped.", len(missing))
            run.sudo().message_post(body=body)
        return True

    def action_force_apply(self):
        return self.action_apply(force=True)

    def action_view_changed(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('operations_research.or_run_line_action')
        action['domain'] = [('run_id', '=', self.id)]
        action['context'] = {'search_default_changed': 1}
        return action


class OrRunLine(models.Model):
    _name = 'or.run.line'
    _description = 'Optimization Result Line'
    _order = 'run_id, sequence, id'

    run_id = fields.Many2one('or.run', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='run_id.company_id', store=True)
    sequence = fields.Integer(default=0)
    res_model = fields.Char(required=True, string='Model')
    res_id = fields.Many2oneReference(model_field='res_model', string='Record ID', required=True)
    res_name = fields.Char(string='Record')
    value_before = fields.Float(string='Current Value')
    value = fields.Float(string='Proposed Value')
    delta = fields.Float(compute='_compute_delta', store=True, string='Change')
    changed = fields.Boolean(compute='_compute_delta', store=True)
    objective_coef = fields.Float(string='Objective Coefficient')
    reduced_cost = fields.Float()
    assigned_res_id = fields.Integer(string='Assigned Resource ID')
    assigned_name = fields.Char(string='Proposed Resource')
    assigned_before_name = fields.Char(string='Current Resource')
    snapshot = fields.Char(help="Value of the target field(s) at solve time, used to detect conflicts.")

    @api.depends('value', 'value_before')
    def _compute_delta(self):
        for line in self:
            line.delta = line.value - line.value_before
            line.changed = abs(line.delta) > 1e-6

    def action_open_record(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.res_model,
            'res_id': self.res_id,
            'view_mode': 'form',
            'target': 'current',
        }


class OrRunConstraint(models.Model):
    _name = 'or.run.constraint'
    _description = 'Optimization Constraint Result'
    _order = 'run_id, id'

    run_id = fields.Many2one('or.run', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='run_id.company_id', store=True)
    constraint_id = fields.Many2one('or.problem.constraint', ondelete='set null')
    name = fields.Char(required=True)
    operator = fields.Selection([('<=', '≤'), ('>=', '≥'), ('=', '=')], required=True)
    rhs = fields.Float(string='Limit')
    activity = fields.Float(string='Used')
    slack = fields.Float()
    dual_value = fields.Float(string='Shadow Price',
                              help="Change of the objective per extra unit of limit (LP only).")
    is_binding = fields.Boolean(compute='_compute_is_binding', store=True, string='Bottleneck')
    usage_percent = fields.Float(compute='_compute_is_binding', store=True, string='Usage (%)')

    @api.depends('slack', 'rhs', 'activity')
    def _compute_is_binding(self):
        for line in self:
            line.is_binding = abs(line.slack) <= 1e-6
            line.usage_percent = (line.activity / line.rhs * 100.0) if line.rhs else 0.0
