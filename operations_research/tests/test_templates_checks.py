import json

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import OrCase


@tagged('post_install', '-at_install', 'operations_research')
class TestTemplates(OrCase):

    def test_template_data_is_consistent(self):
        templates = self.env['or.template'].search([])
        self.assertGreaterEqual(len(templates), 19)
        problem_fields = self.env['or.problem']._fields
        for template in templates:
            config = json.loads(template.config)
            self.assertEqual(config['model'], template.model_name)
            for slot in list(config.get('fields', {})) + list(config.get('values', {})):
                self.assertIn(slot, problem_fields, "%s: unknown slot %s" % (template.name, slot))
            self.assertIn(template.problem_type, dict(problem_fields['problem_type'].selection))
            self.assertTrue(template.summary and template.next_steps)

    def test_missing_app_is_explained(self):
        crm = self.env.ref('operations_research.tpl_leads_teams')
        if 'crm.lead' in self.env:
            self.skipTest("CRM installed")
        self.assertFalse(crm.is_available)
        self.assertIn('Install', crm.missing_info)
        with self.assertRaises(UserError):
            crm.with_user(self.designer).action_use()

    def test_use_template_creates_problem(self):
        template = self.env.ref('operations_research.tpl_territories')
        action = template.with_user(self.designer).action_use()
        problem = self.env['or.problem'].browse(action['res_id'])
        self.assertEqual((problem.problem_type, problem.model_name), ('routing', 'res.partner'))
        self.assertEqual(problem.route_depot_id, self.env.company.partner_id)
        self.assertEqual(len(problem.route_vehicle_ids), 2)
        self.assertEqual(problem.route_lat_field_id.name, 'partner_latitude')
        self.assertIn('Before solving', problem.description)
        checks = dict(problem._or_setup_checks())
        self.assertIn('Link every vehicle', checks.get('blocker', ''))

    def test_missing_field_is_reported_not_fatal(self):
        template = self.env.ref('operations_research.tpl_customers_reps')
        self.assertIn('x_max_accounts', template.missing_info)
        problem = self.env['or.problem'].browse(template.with_user(self.designer).action_use()['res_id'])
        self.assertFalse(problem.assign_capacity_field_id)
        self.assertEqual(problem.assign_field_id.name, 'user_id')
        self.assertIn('x_max_accounts', problem.message_ids[0].body)

    def test_alternative_field_is_used(self):
        self.env['ir.model.fields'].create({'model_id': self.env['ir.model']._get_id('res.users'),
                                            'name': 'x_max_accounts', 'ttype': 'integer',
                                            'field_description': 'Max accounts'})
        template = self.env.ref('operations_research.tpl_customers_reps')
        self.assertFalse(template.missing_info)
        problem = self.env['or.problem'].browse(template.action_use()['res_id'])
        self.assertEqual(problem.assign_capacity_field_id.name, 'x_max_accounts')

    def test_bad_filter_falls_back(self):
        template = self.env['or.template'].create({
            'name': 'Broken', 'problem_type': 'lp', 'model_name': 'x_or_item',
            'config': json.dumps({'model': 'x_or_item', 'values': {'domain': '[("nope", "=", 1)]'},
                                  'fields': {'var_field_id': ['x_qty']}})})
        problem = self.env['or.problem'].browse(template.action_use()['res_id'])
        self.assertEqual(problem.domain, '[]')
        self.assertIn('reset to all records', problem.message_ids[0].body)

    def test_only_designers_use_templates(self):
        template = self.env.ref('operations_research.tpl_territories')
        with self.assertRaises(UserError):
            template.with_user(self.user).action_use()


@tagged('post_install', '-at_install', 'operations_research')
class TestCheckSetup(OrCase):

    def levels(self, problem):
        result = {}
        for level, message in problem._or_setup_checks():
            result.setdefault(level, []).append(message)
        return result

    def test_ready_problem(self):
        _items, problem = self.wyndor()
        checks = self.levels(problem)
        self.assertNotIn('blocker', checks)
        self.assertIn('Ready to solve.', checks['ok'])
        wizard = self.env['or.setup.check'].browse(problem.action_check_setup()['res_id'])
        self.assertTrue(wizard.is_ready)
        self.assertEqual(self.env['or.run'].browse(wizard.action_solve()['res_id']).state, 'done')

    def test_lp_without_limits_is_blocked(self):
        items = self.Item.create([{'x_name': 'a', 'x_price': 1}, {'x_name': 'b'}])
        problem = self.lp(items)
        checks = self.levels(problem)
        self.assertTrue(any('infinite' in m for m in checks['blocker']))
        self.assertTrue(any('no value for the goal' in m for m in checks['warning']))
        wizard = self.env['or.setup.check'].browse(problem.action_check_setup()['res_id'])
        with self.assertRaises(UserError):
            wizard.action_solve()

    def test_no_records_and_bad_config(self):
        _items, problem = self.wyndor()
        problem.domain = "[('id', '=', 0)]"
        self.assertTrue(any('No ' in m for m in self.levels(problem)['blocker']))
        problem.var_field_id = False
        self.assertTrue(any('decision field' in m for m in self.levels(problem)['blocker']))

    def test_assignment_capacity(self):
        tasks = self.Item.create([{'x_name': 't%s' % i, 'x_load': 5} for i in range(4)])
        problem = self.env['or.problem'].create({
            'name': 'A', 'problem_type': 'assignment', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % tasks.ids, 'assign_field_id': self.field('x_assigned_id').id,
            'assign_task_load_field_id': self.field('x_load').id, 'assign_cost_mode': 'constant',
            'assign_capacity_field_id': self.field('x_capacity', 'x_or_machine').id,
            'assign_domain': "[('id', 'in', %s)]" % (self.m1 | self.m2).ids})
        checks = self.levels(problem)
        self.assertTrue(any('Total load 20' in m for m in checks['blocker']))
        problem.assign_all = False
        checks = self.levels(problem)
        self.assertNotIn('blocker', checks)
        problem.assign_capacity_field_id = False
        problem.assign_all = True
        self.assertTrue(any('one record' in m for m in self.levels(problem)['blocker']))

    def test_scheduling_cycle_and_zero_duration(self):
        items = self.Item.create([{'x_name': 'a', 'x_duration': 1}, {'x_name': 'b', 'x_duration': 0}])
        items[0].x_pred_ids = items[1]
        items[1].x_pred_ids = items[0]
        problem = self.env['or.problem'].create({
            'name': 'S', 'problem_type': 'scheduling', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % items.ids, 'sched_duration_field_id': self.field('x_duration').id,
            'sched_predecessor_field_id': self.field('x_pred_ids').id,
            'sched_start_field_id': self.field('x_start').id})
        checks = self.levels(problem)
        self.assertTrue(any('Circular' in m for m in checks['blocker']))
        self.assertTrue(any('no duration' in m for m in checks['warning']))
        problem.write({'sched_time_mode': 'calendar', 'sched_calendar_id': self.make_calendar().id})
        items[1].x_pred_ids = False
        checks = self.levels(problem)
        self.assertTrue(any('working hours a week' in m for m in checks['ok']))

    def test_routing_locations(self):
        depot = self.env['res.partner'].create({'name': 'Depot'})
        nowhere = self.env['res.partner'].create({'name': 'Nowhere'})
        items = self.Item.create([{'x_name': 'x', 'x_partner_id': nowhere.id}])
        problem = self.env['or.problem'].create({
            'name': 'R', 'problem_type': 'routing', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % items.ids, 'route_partner_field_id': self.field('x_partner_id').id,
            'route_depot_id': depot.id, 'route_sequence_field_id': self.field('x_seq').id,
            'route_vehicle_ids': [(0, 0, {'name': 'Van'})]})
        blockers = self.levels(problem)['blocker']
        self.assertTrue(any('Depot' in m for m in blockers))
        self.assertTrue(any('Nowhere' in m for m in blockers))

    def test_plain_user_can_check(self):
        _items, problem = self.wyndor()
        action = problem.with_user(self.user).action_check_setup()
        self.assertEqual(action['res_model'], 'or.setup.check')
