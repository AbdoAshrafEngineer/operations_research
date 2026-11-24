from datetime import datetime

from odoo.tests import TransactionCase, new_test_user

T0 = datetime(2026, 1, 5, 8, 0)


class OrCase(TransactionCase):
    """Builds two throw-away custom models (x_or_item, x_or_machine) so the suite
    runs on any database without extra modules."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        Model = cls.env['ir.model']
        cls.machine_model = Model.create({'name': 'OR Machine', 'model': 'x_or_machine', 'field_id': [
            (0, 0, {'name': 'x_name', 'ttype': 'char', 'field_description': 'Name'}),
            (0, 0, {'name': 'x_capacity', 'ttype': 'float', 'field_description': 'Capacity'}),
            (0, 0, {'name': 'x_rate', 'ttype': 'float', 'field_description': 'Rate'}),
            (0, 0, {'name': 'x_skill', 'ttype': 'selection', 'field_description': 'Skill',
                    'selection': "[('cut', 'Cut'), ('weld', 'Weld')]"}),
        ]})
        cls.item_model = Model.create({'name': 'OR Item', 'model': 'x_or_item', 'field_id': [
            (0, 0, {'name': 'x_name', 'ttype': 'char', 'field_description': 'Name'}),
        ] + [(0, 0, {'name': n, 'ttype': t, 'field_description': n}) for n, t in [
            ('x_qty', 'float'), ('x_qty_int', 'integer'), ('x_selected', 'boolean'), ('x_price', 'float'),
            ('x_hours', 'float'), ('x_weight', 'float'), ('x_load', 'float'), ('x_min', 'float'), ('x_max', 'float'),
            ('x_duration', 'float'), ('x_start', 'datetime'), ('x_end', 'datetime'), ('x_deadline', 'datetime'),
            ('x_seq', 'integer'), ('x_eta', 'datetime')]] + [
            (0, 0, {'name': 'x_skill', 'ttype': 'selection', 'field_description': 'Skill',
                    'selection': "[('cut', 'Cut'), ('weld', 'Weld')]"}),
            (0, 0, {'name': 'x_machine_id', 'ttype': 'many2one', 'relation': 'x_or_machine', 'field_description': 'Machine'}),
            (0, 0, {'name': 'x_assigned_id', 'ttype': 'many2one', 'relation': 'x_or_machine', 'field_description': 'Assigned'}),
            (0, 0, {'name': 'x_vehicle_id', 'ttype': 'many2one', 'relation': 'x_or_machine', 'field_description': 'Vehicle'}),
            (0, 0, {'name': 'x_partner_id', 'ttype': 'many2one', 'relation': 'res.partner', 'field_description': 'Customer'}),
            (0, 0, {'name': 'x_pred_ids', 'ttype': 'many2many', 'relation': 'x_or_item', 'field_description': 'Before',
                    'relation_table': 'x_or_item_pred_rel', 'column1': 'x_item_id', 'column2': 'x_pred_id'}),
        ]})
        for model in (cls.machine_model, cls.item_model):
            cls.env['ir.model.access'].create({'name': model.model, 'model_id': model.id,
                                               'group_id': cls.env.ref('base.group_user').id,
                                               'perm_read': True, 'perm_write': True, 'perm_create': True,
                                               'perm_unlink': True})
        cls.Item = cls.env['x_or_item']
        cls.Machine = cls.env['x_or_machine']
        groups = 'base.group_user,operations_research.group_or_%s'
        cls.user = new_test_user(cls.env, 'or_user', groups=groups % 'user')
        cls.designer = new_test_user(cls.env, 'or_designer', groups=groups % 'designer')
        cls.manager = new_test_user(cls.env, 'or_manager', groups=groups % 'manager')
        cls.outsider = new_test_user(cls.env, 'or_outsider', groups='base.group_user')
        cls.m1 = cls.Machine.create({'x_name': 'M1', 'x_capacity': 10, 'x_rate': 5, 'x_skill': 'cut'})
        cls.m2 = cls.Machine.create({'x_name': 'M2', 'x_capacity': 6, 'x_rate': 3, 'x_skill': 'weld'})

    @classmethod
    def field(cls, name, model='x_or_item'):
        return cls.env['ir.model.fields']._get(model, name)

    def lp(self, items, constraints=(), **vals):
        values = {'name': 'LP', 'problem_type': 'lp', 'model_id': self.item_model.id,
                  'domain': "[('id', 'in', %s)]" % items.ids, 'var_field_id': self.field('x_qty').id,
                  'objective_field_id': self.field('x_price').id, 'objective_sense': 'max'}
        values.update(vals)
        problem = self.env['or.problem'].create(values)
        for cons in constraints:
            cons = dict(cons, problem_id=problem.id)
            for key in ('coef_field_id', 'group_by_field_id'):
                if isinstance(cons.get(key), str):
                    cons[key] = self.field(cons[key]).id
            if isinstance(cons.get('rhs_field_id'), str):
                cons['rhs_field_id'] = self.field(cons['rhs_field_id'], 'x_or_machine').id
            self.env['or.problem.constraint'].create(cons)
        return problem

    def wyndor(self):
        """max 3a + 5b ; a <= 4 ; 2b <= 12 ; 3a + 2b <= 18 -> a=2, b=6, z=36."""
        items = self.Item.create([
            {'x_name': 'A', 'x_price': 3, 'x_hours': 3, 'x_weight': 0, 'x_load': 1},
            {'x_name': 'B', 'x_price': 5, 'x_hours': 2, 'x_weight': 2, 'x_load': 0},
        ])
        problem = self.lp(items, [
            {'name': 'Plant 1', 'coef_field_id': 'x_load', 'rhs_value': 4},
            {'name': 'Plant 2', 'coef_field_id': 'x_weight', 'rhs_value': 12},
            {'name': 'Plant 3', 'coef_field_id': 'x_hours', 'rhs_value': 18},
        ])
        return items, problem

    def solve(self, problem, user=None):
        problem = problem.with_user(user) if user else problem
        return self.env['or.run'].browse(problem.action_run()['res_id'])

    def make_calendar(self, holidays=()):
        """Mon-Fri 08:00-12:00 and 13:00-17:00 UTC, with optional full-day closures."""
        days = []
        for day in range(5):
            days += [(0, 0, {'name': 'AM', 'dayofweek': str(day), 'hour_from': 8, 'hour_to': 12,
                             'day_period': 'morning'}),
                     (0, 0, {'name': 'PM', 'dayofweek': str(day), 'hour_from': 13, 'hour_to': 17,
                             'day_period': 'afternoon'})]
        calendar = self.env['resource.calendar'].create({
            'name': 'OR test hours', 'tz': 'UTC', 'attendance_ids': [(5, 0, 0)] + days})
        for day in holidays:
            self.env['resource.calendar.leaves'].create({
                'name': 'Holiday', 'calendar_id': calendar.id,
                'date_from': day.replace(hour=0, minute=0), 'date_to': day.replace(hour=23, minute=59)})
        return calendar
