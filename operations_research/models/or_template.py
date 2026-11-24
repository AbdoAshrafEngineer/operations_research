import json
import logging

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command, Domain

_logger = logging.getLogger(__name__)


class OrTemplate(models.Model):
    """Ready-made problem configurations for common business decisions.

    ``config`` is JSON::

        {"model": "crm.lead",
         "values": {"domain": "[...]", "objective_sense": "min", ...},
         "fields": {"assign_field_id": ["team_id"],                 # alternatives, first found wins
                    "assign_capacity_field_id": ["crm.team/invoiced_target", "crm.team/x_pipeline_capacity"]},
         "constraints": [{"name": "Cash", "coef_path": "product_id.standard_price", "operator": "<=",
                          "rhs_value": 20000, "group_by": ["partner_id"]}],
         "vehicles": [{"name": "Van 1", "capacity": 1000}],
         "depot": "company", "calendar": "company"}

    A field reference is ``name`` (on the problem model) or ``model/name``. Missing fields,
    filters or apps never block: the problem is created and the gaps are listed for the user.
    """
    _name = 'or.template'
    _description = 'Optimization Template'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    problem_type = fields.Selection(
        [('lp', 'How much to buy or make?'), ('assignment', 'Who does what?'),
         ('scheduling', 'When to do it?'), ('routing', 'Which route to drive?')],
        string='Question', required=True)
    app_name = fields.Char(string='App', translate=True)
    summary = fields.Text(translate=True, help="What it decides, the goal and the limits, in plain words.")
    next_steps = fields.Text(translate=True, help="What to check after creating the problem.")
    model_name = fields.Char(required=True)
    config = fields.Text(required=True)
    is_available = fields.Boolean(compute='_compute_availability', string='App Installed')
    missing_info = fields.Text(compute='_compute_availability', string='Needs')

    @api.constrains('config')
    def _check_config(self):
        for template in self:
            try:
                config = json.loads(template.config)
            except ValueError as e:
                raise ValidationError(self.env._("Invalid template configuration: %s", e)) from e
            if not isinstance(config, dict):
                raise ValidationError(self.env._("Invalid template configuration."))

    def _config(self):
        return json.loads(self.config or '{}')

    # ------------------------------------------------------------------
    # Field resolution
    # ------------------------------------------------------------------
    def _resolve_field(self, refs):
        """First existing field among ``refs`` (list of 'name' or 'model/name')."""
        if isinstance(refs, str):
            refs = [refs]
        Fields = self.env['ir.model.fields'].sudo()
        for ref in refs:
            model, _sep, name = ref.rpartition('/')
            model = model or self.model_name
            if model in self.env and name in self.env[model]._fields:
                field = Fields._get(model, name)
                if field:
                    return field
        return Fields

    def _describe_ref(self, refs):
        refs = [refs] if isinstance(refs, str) else refs
        model, _sep, name = refs[0].rpartition('/')
        model = model or self.model_name
        description = self.env['ir.model']._get(model).name if model in self.env else model
        return self.env._("a field %(name)s on %(model)s", name=name, model=description)

    @api.depends('config', 'model_name')
    def _compute_availability(self):
        for template in self:
            if template.model_name not in self.env:
                template.is_available = False
                template.missing_info = self.env._("Install the %s app.", template.app_name or template.model_name)
                continue
            template.is_available = True
            config = template._config()
            missing = [template._describe_ref(refs) for refs in config.get('fields', {}).values()
                       if not template._resolve_field(refs)]
            template.missing_info = '\n'.join(missing) or False

    # ------------------------------------------------------------------
    # Create a problem
    # ------------------------------------------------------------------
    def _valid_domain(self, model_name, domain):
        """Keep the template filter when it works in this database, else fall back to all records."""
        if model_name not in self.env:
            return '[]', False
        try:
            problem = self.env['or.problem']
            Domain(problem._or_eval_domain(domain))
            self.env[model_name].search_count(problem._or_eval_domain(domain), limit=1)
        except Exception:  # noqa: BLE001 - any error means "not valid here"
            return '[]', True
        return domain, False

    def action_use(self):
        self.ensure_one()
        if not self.env.user.has_group('operations_research.group_or_designer'):
            raise UserError(self.env._("Only Optimization Designers can create problems."))
        if self.model_name not in self.env:
            raise UserError(self.env._("This template needs the %s app. Install it first.",
                                       self.app_name or self.model_name))
        config = self._config()
        notes = []
        vals = dict(config.get('values', {}))
        vals.update({
            'name': self.name,
            'problem_type': self.problem_type,
            'model_id': self.env['ir.model']._get_id(self.model_name),
        })
        for key, model_key in (('domain', self.model_name), ('assign_domain', None)):
            if vals.get(key):
                model_name = model_key
                if model_name is None:
                    assign = self._resolve_field(config.get('fields', {}).get('assign_field_id', []))
                    model_name = assign.relation if assign else None
                if model_name:
                    vals[key], fallback = self._valid_domain(model_name, vals[key])
                    if fallback:
                        notes.append(self.env._("The template filter does not work in this database; "
                                                "it was reset to all records. Set your own filter."))
        for slot, refs in config.get('fields', {}).items():
            field = self._resolve_field(refs)
            if field:
                vals[slot] = field.id
            else:
                notes.append(self.env._("Choose %(what)s (not found: %(ref)s).",
                                        what=self.env['or.problem']._fields[slot].string,
                                        ref=self._describe_ref(refs)))
        if config.get('depot') == 'company':
            vals['route_depot_id'] = self.env.company.partner_id.id
        if config.get('calendar') == 'company' and self.env.company.resource_calendar_id:
            vals['sched_time_mode'] = 'calendar'
            vals['sched_calendar_id'] = self.env.company.resource_calendar_id.id
        vehicles = [Command.create({'name': v['name'], 'capacity': v.get('capacity', 0.0),
                                    'max_distance_km': v.get('max_distance_km', 0.0)})
                    for v in config.get('vehicles', [])]
        if vehicles:
            vals['route_vehicle_ids'] = vehicles
        problem = self.env['or.problem'].create(vals)
        for cons in config.get('constraints', []):
            cons_vals = {key: cons[key] for key in ('name', 'coef_mode', 'coef_path', 'operator', 'rhs_value')
                         if key in cons}
            cons_vals.setdefault('coef_mode', 'field' if cons.get('coef_path') else 'one')
            if cons.get('group_by'):
                group = self._resolve_field(cons['group_by'])
                if not group:
                    notes.append(self.env._("Limit %s was skipped: its grouping field is missing.", cons['name']))
                    continue
                cons_vals['group_by_field_id'] = group.id
            try:
                with self.env.cr.savepoint():
                    problem.constraint_ids = [Command.create(cons_vals)]
            except (ValidationError, UserError) as e:
                notes.append(self.env._("Limit %(name)s was skipped: %(error)s", name=cons['name'], error=e))
        problem.description = self._description_html(notes)
        if notes:
            problem.message_post(body=Markup('<ul>%s</ul>') % Markup().join(
                Markup('<li>%s</li>') % note for note in notes))
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'or.problem',
            'res_id': problem.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _description_html(self, notes):
        parts = [Markup('<p>%s</p>') % escape(self.summary or '')]
        steps = [line for line in (self.next_steps or '').splitlines() if line.strip()]
        if steps or notes:
            parts.append(Markup('<p><strong>%s</strong></p>') % self.env._("Before solving:"))
            parts.append(Markup('<ul>%s</ul>') % Markup().join(
                Markup('<li>%s</li>') % item for item in notes + steps))
        return Markup().join(parts)
