from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import OrCase


@tagged('post_install', '-at_install', 'operations_research')
class TestProblemBuilder(OrCase):

    def builder(self, question, items, user=None, **vals):
        """A builder as the user leaves it after choosing the question and the records."""
        wizard = self.env['or.problem.builder'].with_user(user or self.designer).create(dict(
            question=question, model_id=self.item_model.id, domain="[('id', 'in', %s)]" % items.ids))
        wizard._onchange_suggest()
        wizard.write(vals)
        if 'decide_link_id' in vals:
            wizard._onchange_resource()
        return wizard

    def walk(self, wizard):
        for _step in range(5):
            wizard.action_next()
        self.assertEqual(wizard.step, 'review')
        return self.env['or.problem'].browse(wizard.action_create()['res_id'])

    def test_how_much_end_to_end(self):
        items = self.Item.create([
            {'x_name': 'A', 'x_price': 3, 'x_hours': 3, 'x_weight': 0, 'x_load': 1},
            {'x_name': 'B', 'x_price': 5, 'x_hours': 2, 'x_weight': 2, 'x_load': 0}])
        wizard = self.builder('lp', items)
        self.assertIn('qty', wizard.decide_number_id.name, "the quantity field is guessed")
        self.assertEqual(wizard.goal_path, 'x_price', "the price is guessed as the value to maximise")
        wizard.write({'decide_number_id': self.field('x_qty').id, 'whole_units': False, 'limit_ids': [
            (0, 0, {'name': name, 'coef_path': path, 'rhs_value': amount})
            for name, path, amount in (('Plant 1', 'x_load', 4), ('Plant 2', 'x_weight', 12), ('Plant 3', 'x_hours', 18))]})
        self.assertEqual(wizard.record_count, 2)
        problem = self.walk(wizard)
        self.assertEqual((problem.problem_type, problem.var_type, len(problem.constraint_ids)), ('lp', 'continuous', 3))
        self.assertIn('Built with the Problem Builder', problem.description)
        self.assertAlmostEqual(self.solve(problem).objective_value, 36.0)

    def test_how_much_needs_a_limit(self):
        items = self.Item.create([{'x_name': 'A', 'x_price': 1}])
        wizard = self.builder('lp', items)
        for _step in range(4):
            wizard.action_next()
        self.assertEqual(wizard.step, 'limits')
        with self.assertRaisesRegex(UserError, 'infinite'):
            wizard.action_next()
        wizard.max_mode = 'current'
        wizard.action_next()
        self.assertEqual(wizard.step, 'review')

    def test_who_does_what_guesses_resource_fields(self):
        items = self.Item.create([{'x_name': 't%s' % i, 'x_load': 2} for i in range(3)])
        wizard = self.builder('assignment', items, decide_link_id=self.field('x_assigned_id').id)
        self.assertEqual(wizard.resource_model, 'x_or_machine')
        self.assertEqual(wizard.capacity_field_id.name, 'x_capacity')
        self.assertEqual(wizard.res_cost_field_id.name, 'x_rate')
        self.assertEqual(wizard.assign_goal, 'cost')
        wizard.load_field_id = self.field('x_load')
        problem = self.walk(wizard)
        self.assertEqual((problem.assign_cost_mode, problem.assign_capacity_field_id.name), ('resource', 'x_capacity'))
        self.assertEqual(self.solve(problem).solver_status, 'optimal')

    def test_when_guesses_dates_duration_and_order(self):
        items = self.Item.create([{'x_name': 'a', 'x_duration': 1, 'x_machine_id': self.m1.id}])
        wizard = self.builder('scheduling', items)
        self.assertEqual(wizard.decide_date_id.name, 'x_start')
        self.assertEqual(wizard.end_field_id.name, 'x_end')
        self.assertEqual(wizard.duration_field_id.name, 'x_duration')
        self.assertEqual(wizard.predecessor_field_id.name, 'x_pred_ids')
        self.assertEqual(wizard.deadline_field_id.name, 'x_deadline')
        wizard.write({'resource_field_id': self.field('x_machine_id').id, 'working_hours': False})
        problem = self.walk(wizard)
        self.assertEqual((problem.sched_time_mode, problem.sched_resource_field_id.name), ('continuous', 'x_machine_id'))
        self.assertEqual(self.solve(problem).solver_status, 'optimal')

    def test_which_route_vehicles_from_a_filter(self):
        depot_partner = self.env.company.partner_id
        depot_partner.write({'partner_latitude': 24.63, 'partner_longitude': 46.71})
        customers = self.env['res.partner'].create([
            {'name': 'C%s' % i, 'partner_latitude': 24.63 + 0.02 * i, 'partner_longitude': 46.71} for i in (1, 2)])
        items = self.Item.create([{'x_name': c.name, 'x_partner_id': c.id} for c in customers])
        wizard = self.builder('routing', items)
        self.assertEqual(wizard.partner_field_id.name, 'x_partner_id')
        self.assertEqual(wizard.sequence_field_id.name, 'x_seq')
        self.assertEqual(wizard.eta_field_id.name, 'x_eta')
        wizard.write({'vehicle_field_id': self.field('x_vehicle_id').id,
                      'vehicle_domain': "[('id', 'in', %s)]" % (self.m1 | self.m2).ids})
        self.assertEqual(wizard.vehicle_match_count, 2)
        problem = self.walk(wizard)
        self.assertEqual(problem.route_vehicle_ids.mapped('target_ref'), [self.m1, self.m2])
        problem.time_limit = 1
        self.assertIn(self.solve(problem).solver_status, ('optimal', 'feasible'))

    def test_steps_explain_what_is_missing(self):
        wizard = self.env['or.problem.builder'].with_user(self.designer).create({'question': 'scheduling'})
        wizard.action_next()
        with self.assertRaisesRegex(UserError, 'Choose which records'):
            wizard.action_next()
        wizard.action_back()
        self.assertEqual(wizard.step, 'question')

    def test_summary_in_plain_words(self):
        items = self.Item.create([{'x_name': 'a', 'x_duration': 1}])
        summary = self.builder('scheduling', items).summary
        self.assertIn('o_or_tag_decide', summary)
        self.assertIn('only inside working hours', summary)

    def test_only_designers_create(self):
        items = self.Item.create([{'x_name': 'a', 'x_qty': 1}])
        wizard = self.builder('lp', items, user=self.designer)
        wizard.max_mode = 'current'
        with self.assertRaises(UserError):
            wizard.with_user(self.user).action_create()
