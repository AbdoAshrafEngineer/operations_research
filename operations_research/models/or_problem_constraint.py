from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain


class OrProblemConstraint(models.Model):
    _name = 'or.problem.constraint'
    _description = 'Optimization Constraint'
    _order = 'sequence, id'

    problem_id = fields.Many2one('or.problem', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='problem_id.company_id', store=True)
    model_id = fields.Many2one(related='problem_id.model_id')
    model_name = fields.Char(related='problem_id.model_name')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    name = fields.Char(required=True, translate=True, copy=True)
    coef_mode = fields.Selection(
        [('one', 'Count (1 per record)'), ('field', 'Field value')],
        string='Weight', default='field', required=True,
        help="Each record's decision value is multiplied by this weight and summed.")
    coef_field_id = fields.Many2one(
        'ir.model.fields', string='Weight Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    coef_path = fields.Char(
        string='Weight Value', compute='_compute_coef_path', store=True, readonly=False,
        help="Number counted per unit. Can follow links, e.g. Product > Cost or Product > Volume.")
    domain = fields.Char(string='Applies To', default='[]',
                         help="Restrict the constraint to the records matching this filter.")
    group_by_field_id = fields.Many2one(
        'ir.model.fields', string='One Constraint Per', ondelete='set null',
        domain="[('model_id', '=', model_id), ('store', '=', True), "
               "('ttype', 'in', ('many2one', 'selection', 'char', 'boolean', 'integer'))]",
        help="Create one constraint for each value of this field (e.g. per machine).")
    group_by_relation = fields.Char(related='group_by_field_id.relation')
    operator = fields.Selection([('<=', '≤ At most'), ('>=', '≥ At least'), ('=', '= Exactly')],
                                default='<=', required=True)
    rhs_mode = fields.Selection(
        [('constant', 'Fixed value'), ('group_field', 'Field on the grouped record')],
        string='Limit Type', default='constant', required=True)
    rhs_value = fields.Float(string='Limit')
    rhs_field_id = fields.Many2one(
        'ir.model.fields', string='Limit Field', ondelete='set null',
        domain="[('model', '=', group_by_relation), ('ttype', 'in', ('float', 'integer', 'monetary'))]",
        help="E.g. the capacity field of the machine the records are grouped by.")
    summary = fields.Char(compute='_compute_summary')

    @api.depends('coef_field_id')
    def _compute_coef_path(self):
        for line in self:
            if line.coef_field_id:
                line.coef_path = line.coef_field_id.name

    def _path_label(self, path):
        """Human label of a field path, e.g. 'Product > Cost'."""
        if not path or self.model_name not in self.env:
            return ''
        Model, labels = self.env[self.model_name], []
        for part in path.split('.'):
            field = Model._fields.get(part)
            if field is None:
                return path
            labels.append(field._description_string(self.env))
            if field.type == 'many2one':
                Model = self.env[field.comodel_name]
        return ' > '.join(labels)

    @api.depends('coef_mode', 'coef_field_id', 'coef_path', 'operator', 'rhs_mode', 'rhs_value', 'rhs_field_id',
                 'group_by_field_id')
    def _compute_summary(self):
        for line in self:
            weight = line._path_label(line.coef_path) if line.coef_mode == 'field' else '1'
            limit = (line.rhs_field_id.field_description if line.rhs_mode == 'group_field'
                     else '%g' % line.rhs_value)
            per = (' / %s' % line.group_by_field_id.field_description) if line.group_by_field_id else ''
            line.summary = 'Σ %s × x %s %s%s' % (weight or '?', line.operator, limit or '?', per)

    @api.constrains('coef_field_id', 'group_by_field_id', 'rhs_field_id', 'rhs_mode', 'domain', 'coef_path')
    def _check_fields(self):
        for line in self:
            if line.coef_path and line.model_name in self.env:
                line.problem_id._or_check_path(line.model_name, line.coef_path)
            for field in (line.coef_field_id, line.group_by_field_id):
                if field and field.model_id != line.problem_id.model_id:
                    raise ValidationError(self.env._(
                        "Field %(field)s does not belong to model %(model)s.",
                        field=field.field_description, model=line.problem_id.model_id.name))
            if line.rhs_mode == 'group_field':
                if line.group_by_field_id.ttype != 'many2one':
                    raise ValidationError(self.env._(
                        "Constraint %s: a limit taken from a field requires grouping by a "
                        "many2one field.", line.name))
                if line.rhs_field_id and line.rhs_field_id.model != line.group_by_field_id.relation:
                    raise ValidationError(self.env._(
                        "Constraint %s: the limit field must belong to %s.",
                        line.name, line.group_by_field_id.relation))
            try:
                Domain(line.problem_id._or_eval_domain(line.domain))
            except Exception as e:  # noqa: BLE001
                raise ValidationError(self.env._("Invalid filter on constraint %(name)s: %(error)s",
                                                 name=line.name, error=e)) from e

    def _or_check_config(self):
        self.ensure_one()
        if self.coef_mode == 'field' and not self.coef_path:
            raise UserError(self.env._("Constraint %s: choose the weight field.", self.name))
        if self.rhs_mode == 'group_field' and not (self.group_by_field_id and self.rhs_field_id):
            raise UserError(self.env._("Constraint %s: choose the group field and the limit field.",
                                       self.name))

    def _group_value(self, record):
        """Return (key, label, value) of the group of ``record``."""
        field = self.group_by_field_id
        value = record[field.name]
        if field.ttype == 'many2one':
            return value.id or False, value.display_name or self.env._("Undefined"), value
        if field.ttype == 'selection':
            selection = dict(record._fields[field.name]._description_selection(self.env))
            return value, selection.get(value, self.env._("Undefined")), value
        return value, str(value) if value not in (False, None) else self.env._("Undefined"), value

    def _or_build_rows(self, records, log):
        """Build solver constraint rows for this line over ``records``."""
        self.ensure_one()
        problem = self.problem_id
        subset = records
        domain = problem._or_eval_domain(self.domain)
        if domain:
            subset = records.filtered_domain(domain)

        def coef(record):
            if self.coef_mode == 'one':
                return 1.0
            return problem._or_path_num(record, self.coef_path, 0.0)

        if not self.group_by_field_id:
            return [{
                'key': (self.id, None),
                'line_id': self.id,
                'name': self.name,
                'coefs': {rec.id: coef(rec) for rec in subset},
                'op': self.operator,
                'rhs': self.rhs_value,
            }]
        groups = {}
        for record in subset:
            key, label, value = self._group_value(record)
            group = groups.setdefault(key, {'label': label, 'value': value, 'coefs': {}})
            group['coefs'][record.id] = coef(record)
        rows = []
        for key, group in groups.items():
            if self.rhs_mode == 'group_field':
                if not key:
                    log.append(self.env._(
                        "Constraint %(name)s skipped for records without %(field)s.",
                        name=self.name, field=self.group_by_field_id.field_description))
                    continue
                rhs = problem._or_num(group['value'], self.rhs_field_id, 0.0)
            else:
                rhs = self.rhs_value
            rows.append({
                'key': (self.id, key),
                'line_id': self.id,
                'name': '%s / %s' % (self.name, group['label']),
                'coefs': group['coefs'],
                'op': self.operator,
                'rhs': rhs,
            })
        return rows
