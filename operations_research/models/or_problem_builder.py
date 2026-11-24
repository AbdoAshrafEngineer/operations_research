"""Problem Builder: build a new problem by answering one plain question per screen.

First principles: a problem is "what Odoo writes on each record" (DECIDE), "what best means"
(GOAL) and "what must stay true" (LIMITS). The kind of field written decides the question:
a number -> how much, a link -> who does what, a date -> when, a visit order -> which route.
Every answer is pre-filled with the most likely field, found from the model's own field names.
"""
from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command

NUMBER = ('float', 'integer', 'monetary')
SKIP = {'id', 'create_uid', 'write_uid', 'create_date', 'write_date', 'company_id', 'currency_id',
        'sequence', 'color', 'active', 'display_name'}
KEYWORDS = {
    'decide_number': ['qty_to_order', 'product_qty', 'product_uom_qty', 'quantity', 'qty', 'amount'],
    'decide_link': ['team', 'workcenter', 'driver', 'user', 'technician', 'employee', 'assigned', 'responsible'],
    'decide_date': ['date_start', 'schedule_date', 'start', 'planned', 'scheduled', 'begin'],
    'goal_value': ['list_price', 'price_unit', 'expected_revenue', 'revenue', 'price', 'amount', 'value', 'margin'],
    'goal_cost': ['standard_price', 'cost', 'price'],
    'res_cost': ['costs_hour', 'cost', 'rate', 'price', 'wage'],
    'res_capacity': ['capacity', 'hours_available', 'available', 'max', 'target', 'quota', 'limit'],
    'load': ['duration_expected', 'duration', 'hours', 'expected_revenue', 'revenue', 'weight', 'volume', 'qty'],
    'duration': ['duration_expected', 'allocated_hours', 'planned_hours', 'duration', 'hours', 'time'],
    'resource': ['workcenter', 'technician', 'user', 'employee', 'machine', 'equipment', 'resource', 'team'],
    'predecessor': ['blocked_by', 'depend_on', 'predecessor', 'before', 'blocked', 'depend'],
    'deadline': ['date_deadline', 'deadline', 'due', 'commitment'],
    'end': ['date_finished', 'schedule_end', 'date_end', 'stop', 'end', 'finish'],
    'partner': ['partner_shipping_id', 'partner_id', 'customer', 'contact', 'partner'],
    'demand': ['shipping_weight', 'weight', 'volume', 'pallet', 'quantity', 'qty'],
    'sequence': ['stop', 'sequence', 'seq', 'order'],
    'eta': ['scheduled_date', 'commitment_date', 'arrival', 'eta', 'schedule_date', 'date'],
    'vehicle': ['vehicle', 'driver', 'truck', 'user'],
}
STEPS = ['question', 'records', 'decide', 'goal', 'limits', 'review']


class OrProblemBuilder(models.TransientModel):
    _name = 'or.problem.builder'
    _description = 'Problem Builder'

    step = fields.Selection([(s, s) for s in STEPS], default='question', required=True)
    question = fields.Selection([
        ('lp', 'How much? A quantity (to buy, make, ship)'),
        ('assignment', 'Who does what? A person, team or machine'),
        ('scheduling', 'When? A start date'),
        ('routing', 'Which route? Vans and stops'),
    ], default='lp', required=True)
    name = fields.Char()
    # 2. records
    model_id = fields.Many2one('ir.model', string='Records', ondelete='cascade',
                               domain="[('transient', '=', False), ('model', 'not like', 'ir.%'), "
                                      "('model', 'not like', 'or.%'), ('model', 'not like', 'mail.%')]")
    model_name = fields.Char(related='model_id.model')
    domain = fields.Char(string='Filter', default='[]')
    record_count = fields.Integer(compute='_compute_record_count')
    # 3. decide
    decide_number_id = fields.Many2one('ir.model.fields', string='Number to Fill', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('store', '=', True), ('ttype', 'in', ('float', 'integer', 'monetary', 'boolean'))]")
    decide_link_id = fields.Many2one('ir.model.fields', string='Link to Fill', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('store', '=', True), ('ttype', '=', 'many2one')]")
    decide_date_id = fields.Many2one('ir.model.fields', string='Start Date to Fill', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('store', '=', True), ('ttype', '=', 'datetime')]")
    end_field_id = fields.Many2one('ir.model.fields', string='End Date to Fill (optional)', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('store', '=', True), ('ttype', '=', 'datetime')]")
    vehicle_field_id = fields.Many2one('ir.model.fields', string='Write the Vehicle Into', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('store', '=', True), ('ttype', '=', 'many2one')]")
    sequence_field_id = fields.Many2one('ir.model.fields', string='Write the Stop Number Into', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('store', '=', True), ('ttype', '=', 'integer')]")
    eta_field_id = fields.Many2one('ir.model.fields', string='Write the Arrival Time Into', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('store', '=', True), ('ttype', '=', 'datetime')]")
    resource_model = fields.Char(compute='_compute_resource_model')
    # 4. goal
    goal_sense = fields.Selection([('max', 'As much as possible'), ('min', 'As little as possible')],
                                  string='Best Means', default='max', required=True)
    goal_path = fields.Char(string='Of This Value')
    assign_goal = fields.Selection([('spread', 'Just give every record a good match'),
                                    ('cost', 'Lowest total cost')], string='Best Means', default='spread', required=True)
    res_cost_field_id = fields.Many2one('ir.model.fields', string='Cost of Each Choice', ondelete='cascade',
        domain="[('model', '=', resource_model), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    sched_goal = fields.Selection([('makespan', 'Finish everything as early as possible'),
                                   ('tardiness', 'Be late as little as possible'),
                                   ('completion', 'Finish each job early')], string='Best Means', default='makespan',
                                  required=True)
    deadline_field_id = fields.Many2one('ir.model.fields', string='Due Date', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'datetime')]")
    route_balance = fields.Boolean(string='Keep routes about the same length')
    # 5. limits
    whole_units = fields.Boolean(string='Whole units only', default=True)
    max_mode = fields.Selection([('none', 'No maximum'), ('current', 'Never more than today\'s value'),
                                 ('field', 'Never more than another field')], string='Maximum', default='none',
                                required=True)
    max_field_id = fields.Many2one('ir.model.fields', string='Maximum Field', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    limit_ids = fields.One2many('or.problem.builder.limit', 'builder_id', string='Limits')
    capacity_field_id = fields.Many2one('ir.model.fields', string='How Much Each Can Take', ondelete='cascade',
        domain="[('model', '=', resource_model), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    load_field_id = fields.Many2one('ir.model.fields', string='How Much Each Record Uses', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    resource_domain = fields.Char(string='Choose Only Among', default='[]')
    assign_all = fields.Boolean(string='Every record must get one', default=True)
    duration_field_id = fields.Many2one('ir.model.fields', string='How Long Each Job Takes', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    duration_unit = fields.Selection([('minutes', 'Minutes'), ('hours', 'Hours'), ('days', 'Days')],
                                     default='hours', required=True)
    resource_field_id = fields.Many2one('ir.model.fields', string='One Job at a Time Per', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'many2one')]")
    predecessor_field_id = fields.Many2one('ir.model.fields', string='Must Wait For', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('many2one', 'many2many')), ('relation', '=', model_name)]")
    working_hours = fields.Boolean(string='Only inside working hours', default=True)
    calendar_id = fields.Many2one('resource.calendar', string='Working Hours',
                                  default=lambda self: self.env.company.resource_calendar_id)
    partner_field_id = fields.Many2one('ir.model.fields', string='Address Of Each Stop', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'many2one'), ('relation', '=', 'res.partner')]")
    demand_field_id = fields.Many2one('ir.model.fields', string='Load Of Each Stop', ondelete='cascade',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    vehicle_count = fields.Integer(string='Number of Vehicles', default=2)
    vehicle_capacity = fields.Float(string='Each Vehicle Carries (0 = no limit)')
    vehicle_model = fields.Char(related='vehicle_field_id.relation')
    vehicle_domain = fields.Char(string='Vehicles Are', default='[]')
    vehicle_match_count = fields.Integer(compute='_compute_vehicle_match_count')
    # 6. review
    summary = fields.Html(compute='_compute_summary', sanitize=False)

    # ------------------------------------------------------------------ computes
    @api.depends('model_name', 'domain')
    def _compute_record_count(self):
        Problem = self.env['or.problem']
        for wiz in self:
            count = 0
            if wiz.model_name and wiz.model_name in self.env:
                try:
                    count = self.env[wiz.model_name].search_count(Problem._or_eval_domain(wiz.domain))
                except Exception:  # noqa: BLE001 - a half-typed filter counts nothing
                    count = 0
            wiz.record_count = count

    @api.depends('decide_link_id', 'vehicle_field_id', 'question')
    def _compute_resource_model(self):
        for wiz in self:
            wiz.resource_model = wiz.decide_link_id.relation if wiz.question == 'assignment' else False

    @api.depends('vehicle_model', 'vehicle_domain')
    def _compute_vehicle_match_count(self):
        Problem = self.env['or.problem']
        for wiz in self:
            count = 0
            if wiz.vehicle_model and wiz.vehicle_model in self.env:
                try:
                    count = self.env[wiz.vehicle_model].search_count(Problem._or_eval_domain(wiz.vehicle_domain))
                except Exception:  # noqa: BLE001
                    count = 0
            wiz.vehicle_match_count = count

    # ------------------------------------------------------------------ suggestions
    def _or_candidates(self, model, ttypes, relation=None, same_model=False):
        if not model or model not in self.env:
            return self.env['ir.model.fields']
        domain = [('model', '=', model), ('ttype', 'in', list(ttypes)), ('name', 'not in', list(SKIP)),
                  ('name', 'not like', 'message_%'), ('name', 'not like', 'activity_%')]
        if relation:
            domain.append(('relation', '=', relation))
        if same_model:
            domain.append(('relation', '=', model))
        return self.env['ir.model.fields'].sudo().search(domain)

    def _best(self, role, model, ttypes, relation=None, same_model=False, writable=False, exclude=()):
        """Most likely field for ``role``, scored on its technical name and its label."""
        best, best_score = self.env['ir.model.fields'], 0
        for field in self._or_candidates(model, ttypes, relation, same_model):
            if field in exclude or 'batch' in field.name:
                continue
            if writable:
                orm_field = self.env[model]._fields.get(field.name)
                if orm_field is None or not orm_field.store or (orm_field.compute and not orm_field.inverse
                                                                and orm_field.readonly) or orm_field.related:
                    continue
            name, label = field.name.lower(), (field.field_description or '').lower()
            score = 0
            for rank, word in enumerate(KEYWORDS[role]):
                weight = len(KEYWORDS[role]) - rank
                if name == word or name == word + '_id':
                    score = max(score, 10 + weight)
                elif word in name:
                    score = max(score, 5 + weight)
                elif word.replace('_', ' ') in label:
                    score = max(score, 2 + weight)
            if score > best_score:
                best, best_score = field, score
        return best

    def _goal_path_guess(self, model, sense):
        Model = self.env[model]
        product = Model._fields.get('product_id')
        if product is not None and product.type == 'many2one':
            return 'product_id.list_price' if sense == 'max' else 'product_id.standard_price'
        field = self._best('goal_value' if sense == 'max' else 'goal_cost', model, NUMBER)
        return field.name or False

    @api.onchange('model_id', 'question')
    def _onchange_suggest(self):
        model = self.model_name
        if not model or model not in self.env:
            return
        q = self.question
        if q == 'lp':
            self.decide_number_id = self._best('decide_number', model, NUMBER + ('boolean',), writable=True)
            self.goal_path = self._goal_path_guess(model, self.goal_sense)
            self.whole_units = self.decide_number_id.ttype != 'float' or 'qty' in (self.decide_number_id.name or '')
        elif q == 'assignment':
            self.decide_link_id = self._best('decide_link', model, ('many2one',), writable=True)
            self.load_field_id = self._best('load', model, NUMBER)
        elif q == 'scheduling':
            self.decide_date_id = self._best('decide_date', model, ('datetime',), writable=True)
            self.end_field_id = self._best('end', model, ('datetime',), writable=True, exclude=self.decide_date_id)
            self.duration_field_id = self._best('duration', model, NUMBER)
            name = self.duration_field_id.name or ''
            self.duration_unit = 'minutes' if 'expected' in name or 'minute' in name else 'hours'
            self.resource_field_id = self._best('resource', model, ('many2one',))
            self.predecessor_field_id = self._best('predecessor', model, ('many2one', 'many2many'), same_model=True)
            self.deadline_field_id = self._best('deadline', model, ('datetime',), exclude=self.decide_date_id)
        elif q == 'routing':
            self.partner_field_id = self._best('partner', model, ('many2one',), relation='res.partner')
            self.demand_field_id = self._best('demand', model, NUMBER)
            self.sequence_field_id = self._best('sequence', model, ('integer',), writable=True)
            self.eta_field_id = self._best('eta', model, ('datetime',), writable=True)
            self.vehicle_field_id = False

    @api.onchange('decide_link_id')
    def _onchange_resource(self):
        rel = self.decide_link_id.relation
        if self.question == 'assignment' and rel:
            self.res_cost_field_id = self._best('res_cost', rel, NUMBER)
            self.capacity_field_id = self._best('res_capacity', rel, NUMBER)
            self.assign_goal = 'cost' if self.res_cost_field_id else 'spread'
            if rel == 'res.users':
                self.resource_domain = "[('share', '=', False)]"

    @api.onchange('goal_sense')
    def _onchange_goal_sense(self):
        if self.question == 'lp' and self.model_name in self.env:
            self.goal_path = self._goal_path_guess(self.model_name, self.goal_sense)

    @api.onchange('vehicle_field_id')
    def _onchange_vehicle_field(self):
        if self.vehicle_field_id.relation == 'res.users':
            self.vehicle_domain = "[('share', '=', False)]"

    # ------------------------------------------------------------------ navigation
    def _check_step(self):
        """Stop at the current step with a plain sentence when something essential is missing."""
        _ = self.env._
        q, step = self.question, self.step
        if step == 'records':
            if not self.model_id:
                raise UserError(_("Choose which records to plan (for example Work Order or Lead)."))
            if not self.record_count:
                raise UserError(_("No record matches. Remove or change the filter."))
        if step == 'decide':
            target = {'lp': self.decide_number_id, 'assignment': self.decide_link_id,
                      'scheduling': self.decide_date_id}.get(q)
            if q == 'routing':
                if not (self.vehicle_field_id or self.sequence_field_id or self.eta_field_id):
                    raise UserError(_("Choose at least one field to write: the vehicle, the stop number or the arrival time."))
                for field in (self.vehicle_field_id, self.sequence_field_id, self.eta_field_id):
                    if field:
                        self.env['or.problem']._check_writable_field(field)
            elif not target:
                raise UserError(_("Choose the field Odoo should fill in."))
            else:
                self.env['or.problem']._check_writable_field(target)
            if q == 'scheduling' and self.end_field_id:
                self.env['or.problem']._check_writable_field(self.end_field_id)
        if step == 'goal':
            if q == 'lp' and self.goal_path:
                self.env['or.problem']._or_check_path(self.model_name, self.goal_path)
            if q == 'assignment' and self.assign_goal == 'cost' and not self.res_cost_field_id:
                raise UserError(_("Choose the cost of each choice, or pick 'Just give every record a good match'."))
            if q == 'scheduling' and self.sched_goal == 'tardiness' and not self.deadline_field_id:
                raise UserError(_("Being late needs a due date: choose the due date field."))
        if step == 'limits':
            if q == 'lp':
                if self.max_mode == 'field' and not self.max_field_id:
                    raise UserError(_("Choose the maximum field."))
                if self.max_mode == 'none' and not self.limit_ids:
                    raise UserError(_("Add at least one limit (for example the budget), or a maximum: "
                                      "otherwise the best plan would be infinite."))
                for limit in self.limit_ids:
                    if limit.coef_path:
                        self.env['or.problem']._or_check_path(self.model_name, limit.coef_path)
            if q == 'scheduling' and not self.duration_field_id:
                raise UserError(_("Choose how long each job takes."))
            if q == 'scheduling' and self.working_hours and not self.calendar_id:
                raise UserError(_("Choose the working hours."))
            if q == 'routing':
                if not self.partner_field_id:
                    raise UserError(_("Choose the address of each stop."))
                if self.vehicle_field_id and not self.vehicle_match_count:
                    raise UserError(_("No record matches 'Vehicles Are': change that filter."))
                if not self.vehicle_field_id and self.vehicle_count < 1:
                    raise UserError(_("You need at least one vehicle."))

    def _reopen(self):
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'new', 'name': self.env._("Build a Problem"),
                'context': {'dialog_size': 'extra-large'}}

    def action_next(self):
        self.ensure_one()
        self._check_step()
        self.step = STEPS[min(STEPS.index(self.step) + 1, len(STEPS) - 1)]
        return self._reopen()

    def action_back(self):
        self.ensure_one()
        self.step = STEPS[max(STEPS.index(self.step) - 1, 0)]
        return self._reopen()

    # ------------------------------------------------------------------ summary
    def _path_label(self, path):
        """'product_id.list_price' -> 'Product > Sales Price' (the labels the user sees)."""
        if not path or not self.model_name or self.model_name not in self.env:
            return path or ''
        Model, labels = self.env[self.model_name], []
        for part in path.split('.'):
            field = Model._fields.get(part)
            if field is None:
                return path
            labels.append(field._description_string(self.env))
            if field.relational:
                Model = self.env[field.comodel_name]
        return ' > '.join(labels)

    @staticmethod
    def _number(value):
        return '{:,.0f}'.format(value) if value == int(value) else '{:,.2f}'.format(value)


    @api.depends('question', 'model_id', 'record_count', 'decide_number_id', 'decide_link_id', 'decide_date_id',
                 'goal_sense', 'goal_path', 'assign_goal', 'res_cost_field_id', 'sched_goal', 'limit_ids',
                 'max_mode', 'capacity_field_id', 'duration_field_id', 'resource_field_id', 'predecessor_field_id',
                 'working_hours', 'partner_field_id', 'vehicle_field_id', 'vehicle_count', 'vehicle_capacity',
                 'vehicle_match_count', 'sequence_field_id', 'eta_field_id')
    def _compute_summary(self):
        _ = self.env._
        for wiz in self:
            if not wiz.model_id:
                wiz.summary = False
                continue
            records = _("%(count)s %(model)s record(s)", count=wiz.record_count, model=wiz.model_id.name)
            decide, goal, limits = '', '', []
            q = wiz.question
            if q == 'lp':
                decide = _("Odoo fills %(field)s on %(records)s.", field=wiz.decide_number_id.field_description or '?', records=records)
                senses = dict(wiz._fields['goal_sense']._description_selection(self.env))
                goal = _("Goal: %(sense)s %(value)s.", sense=senses[wiz.goal_sense].lower(),
                         value=wiz._path_label(wiz.goal_path) or _("(no value chosen)"))
                ops = dict(self.env['or.problem.builder.limit']._fields['operator']._description_selection(self.env))
                limits = [_("%(name)s %(op)s %(amount)s", name=l.name, op=ops[l.operator],
                            amount=wiz._number(l.rhs_value)) for l in wiz.limit_ids]
                if wiz.max_mode == 'current':
                    limits.append(_("never more than today's value"))
                elif wiz.max_mode == 'field' and wiz.max_field_id:
                    limits.append(_("never more than %s", wiz.max_field_id.field_description))
                if wiz.whole_units:
                    limits.append(_("whole units only"))
            elif q == 'assignment':
                decide = _("Odoo chooses %(field)s for %(records)s.", field=wiz.decide_link_id.field_description or '?', records=records)
                goal = (_("Goal: the lowest total %s.", wiz.res_cost_field_id.field_description) if wiz.assign_goal == 'cost'
                        else _("Goal: give every record a good match."))
                if wiz.capacity_field_id:
                    limits.append(_("each one takes at most its %s", wiz.capacity_field_id.field_description))
                else:
                    limits.append(_("each one takes at most one record"))
            elif q == 'scheduling':
                decide = _("Odoo sets %(field)s on %(records)s.", field=wiz.decide_date_id.field_description or '?', records=records)
                goal = _("Goal: %s.", dict(wiz._fields['sched_goal']._description_selection(self.env))[wiz.sched_goal].lower())
                if wiz.resource_field_id:
                    limits.append(_("one job at a time per %s", wiz.resource_field_id.field_description))
                if wiz.predecessor_field_id:
                    limits.append(_("jobs wait for their %s", wiz.predecessor_field_id.field_description))
                if wiz.working_hours:
                    limits.append(_("only inside working hours"))
            else:
                written = [f.field_description for f in (wiz.vehicle_field_id, wiz.sequence_field_id, wiz.eta_field_id) if f]
                decide = _("Odoo plans the route of %(records)s and writes %(fields)s.", records=records, fields=', '.join(written))
                goal = _("Goal: the fewest kilometres.")
                count = wiz.vehicle_match_count if wiz.vehicle_field_id else wiz.vehicle_count
                limits.append(_("%s vehicle(s)", count))
                if wiz.vehicle_capacity:
                    limits.append(_("each carries at most %s", wiz._number(wiz.vehicle_capacity)))
            rows = [('decide', _("Decide"), decide), ('goal', _("Goal"), goal),
                    ('limits', _("Limits"), ', '.join(limits) or _("none"))]
            wiz.summary = Markup('').join(
                Markup('<p class="o_or_summary_row"><span class="o_or_tag o_or_tag_%s">%s</span><span>%s</span></p>')
                % (key, label, escape(text)) for key, label, text in rows)

    # ------------------------------------------------------------------ create
    def _problem_vals(self):
        q = self.question
        vals = {'name': self.name or self.env._("%(model)s plan", model=self.model_id.name),
                'problem_type': q, 'model_id': self.model_id.id, 'domain': self.domain or '[]'}
        if q == 'lp':
            field = self.decide_number_id
            vals.update({
                'var_field_id': field.id,
                'var_type': 'binary' if field.ttype == 'boolean' else ('integer' if self.whole_units else 'continuous'),
                'objective_sense': self.goal_sense, 'objective_path': self.goal_path or False,
                'ub_infinite': self.max_mode == 'none',
                'ub_field_id': (field.id if self.max_mode == 'current' else
                                self.max_field_id.id if self.max_mode == 'field' else False),
                'constraint_ids': [Command.create({
                    'name': limit.name, 'coef_mode': 'field' if limit.coef_path else 'one',
                    'coef_path': limit.coef_path or False, 'operator': limit.operator, 'rhs_value': limit.rhs_value})
                    for limit in self.limit_ids],
            })
        elif q == 'assignment':
            cost = self.assign_goal == 'cost'
            vals.update({
                'assign_field_id': self.decide_link_id.id, 'objective_sense': 'min',
                'assign_cost_mode': 'resource' if cost else 'constant',
                'assign_resource_cost_field_id': self.res_cost_field_id.id if cost else False,
                'assign_capacity_field_id': self.capacity_field_id.id or False,
                'assign_task_load_field_id': self.load_field_id.id if self.capacity_field_id else False,
                'assign_domain': self.resource_domain or '[]', 'assign_all': self.assign_all,
            })
        elif q == 'scheduling':
            vals.update({
                'sched_start_field_id': self.decide_date_id.id, 'sched_end_field_id': self.end_field_id.id or False,
                'sched_duration_field_id': self.duration_field_id.id, 'sched_duration_unit': self.duration_unit,
                'sched_resource_field_id': self.resource_field_id.id or False,
                'sched_predecessor_field_id': self.predecessor_field_id.id or False,
                'sched_deadline_field_id': self.deadline_field_id.id or False, 'sched_objective': self.sched_goal,
                'sched_time_mode': 'calendar' if self.working_hours else 'continuous',
                'sched_calendar_id': self.calendar_id.id or False, 'sched_start_mode': 'now',
            })
        else:
            if self.vehicle_field_id:
                Vehicle = self.env[self.vehicle_model]
                targets = Vehicle.search(self.env['or.problem']._or_eval_domain(self.vehicle_domain), limit=50)
                vehicles = [Command.create({'name': rec.display_name, 'capacity': self.vehicle_capacity,
                                            'target_ref': '%s,%s' % (rec._name, rec.id)}) for rec in targets]
            else:
                vehicles = [Command.create({'name': self.env._("Vehicle %s", i + 1), 'capacity': self.vehicle_capacity})
                            for i in range(self.vehicle_count)]
            vals.update({
                'route_location_mode': 'partner', 'route_partner_field_id': self.partner_field_id.id,
                'route_demand_field_id': self.demand_field_id.id or False,
                'route_depot_id': self.env.company.partner_id.id, 'route_departure_mode': 'now',
                'route_vehicle_field_id': self.vehicle_field_id.id or False,
                'route_sequence_field_id': self.sequence_field_id.id or False,
                'route_eta_field_id': self.eta_field_id.id or False, 'route_balance': self.route_balance,
                'route_vehicle_ids': vehicles,
            })
        return vals

    def action_create(self):
        self.ensure_one()
        if not self.env.user.has_group('operations_research.group_or_designer'):
            raise UserError(self.env._("Only Optimization Designers can create problems."))
        for step in STEPS[1:-1]:
            self.step = step
            self._check_step()
        self.step = 'review'
        problem = self.env['or.problem'].create(self._problem_vals())
        problem.description = Markup('<p><strong>%s</strong></p>%s<p>%s</p>') % (
            self.env._("Built with the Problem Builder"), self.summary,
            self.env._("Next: click Check Setup, then Solve."))
        return {'type': 'ir.actions.act_window', 'res_model': 'or.problem', 'res_id': problem.id,
                'view_mode': 'form', 'target': 'current'}


class OrProblemBuilderLimit(models.TransientModel):
    _name = 'or.problem.builder.limit'
    _description = 'Problem Builder Limit'

    builder_id = fields.Many2one('or.problem.builder', required=True, ondelete='cascade')
    model_name = fields.Char(related='builder_id.model_name')
    name = fields.Char(string='Limit', required=True)
    coef_path = fields.Char(string='Each Record Uses', help="Leave empty to count 1 per unit.")
    operator = fields.Selection([('<=', 'at most'), ('>=', 'at least'), ('=', 'exactly')], default='<=', required=True)
    rhs_value = fields.Float(string='Amount')
