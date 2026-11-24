from datetime import datetime as D

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import T0, OrCase


@tagged('post_install', '-at_install', 'operations_research')
class TestBenefit(OrCase):

    def test_lp_goal_compared_with_current_plan(self):
        items, problem = self.wyndor()
        items.write({'x_qty': 1})  # current plan: 3 + 5 = 8
        run = self.solve(problem)
        kpi = run.kpi_ids
        self.assertEqual((kpi.before, kpi.after, kpi.better), (8.0, 36.0, 'higher'))
        self.assertAlmostEqual(kpi.delta_percent, 350.0)
        self.assertTrue(kpi.improved)
        self.assertEqual(run.kpi_headline, 'Goal value: current plan 8 → proposal 36 (+350%)')
        self.assertTrue(run.kpi_improved)

    def test_current_plan_breaking_limits_is_not_compared(self):
        items, problem = self.wyndor()
        items.write({'x_qty': 10})  # 3a + 2b = 50 > 18: breaks two plants, goal 80 looks "better"
        run = self.solve(problem)
        broken = run.kpi_ids.filtered(lambda k: k.name == 'Limits broken')
        self.assertEqual((broken.before, broken.after), (3.0, 0.0))
        self.assertIn('breaks 3 limit(s) (Plant 1, Plant 2, Plant 3)', run.kpi_headline)
        goal = run.kpi_ids.filtered(lambda k: k.name == 'Goal value')
        self.assertFalse(goal.has_baseline)
        self.assertTrue(run.kpi_improved)

    def test_bounds_count_as_limits(self):
        items, problem = self.wyndor()
        items.write({'x_qty': -1})
        run = self.solve(problem)
        self.assertIn('minimum/maximum of 2 record(s)', run.kpi_headline)

    def test_overlapping_current_schedule_is_not_compared(self):
        items = self.Item.create([{'x_name': 'a', 'x_duration': 2, 'x_machine_id': self.m1.id, 'x_start': T0},
                                  {'x_name': 'b', 'x_duration': 2, 'x_machine_id': self.m1.id, 'x_start': T0}])
        problem = self.env['or.problem'].create({
            'name': 'S', 'problem_type': 'scheduling', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % items.ids, 'sched_duration_field_id': self.field('x_duration').id,
            'sched_resource_field_id': self.field('x_machine_id').id,
            'sched_start_field_id': self.field('x_start').id, 'sched_start_mode': 'fixed', 'sched_start_date': T0})
        run = self.solve(problem)
        self.assertIn('M1 does two jobs at once', run.kpi_headline)
        self.assertFalse(run.kpi_ids.filtered(lambda k: k.name == 'Hours until everything is done').has_baseline)

    def test_assignment_cost(self):
        tasks = self.Item.create([{'x_name': 't1', 'x_hours': 2, 'x_assigned_id': self.m1.id},
                                  {'x_name': 't2', 'x_hours': 1, 'x_assigned_id': self.m1.id}])
        problem = self.env['or.problem'].create({
            'name': 'A', 'problem_type': 'assignment', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % tasks.ids, 'assign_field_id': self.field('x_assigned_id').id,
            'objective_sense': 'min', 'assign_cost_mode': 'product', 'assign_resource_cost_field_id': self.field('x_rate', 'x_or_machine').id,
            'assign_task_weight_field_id': self.field('x_hours').id,
            'assign_capacity_field_id': self.field('x_capacity', 'x_or_machine').id,
            'assign_domain': "[('id', 'in', %s)]" % (self.m1 | self.m2).ids})
        run = self.solve(problem)
        cost = run.kpi_ids.filtered(lambda k: k.name == 'Total cost')
        self.assertEqual((cost.before, cost.after), (15.0, 9.0), "rate 5 x 3h now, rate 3 x 3h proposed")
        assigned = run.kpi_ids.filtered(lambda k: k.name == 'Records assigned')
        self.assertEqual((assigned.before, assigned.after), (2, 2))

    def test_scheduling_with_and_without_current_dates(self):
        items = self.Item.create([{'x_name': 'a', 'x_duration': 2, 'x_machine_id': self.m1.id},
                                  {'x_name': 'b', 'x_duration': 2, 'x_machine_id': self.m2.id}])
        problem = self.env['or.problem'].create({
            'name': 'S', 'problem_type': 'scheduling', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % items.ids, 'sched_duration_field_id': self.field('x_duration').id,
            'sched_resource_field_id': self.field('x_machine_id').id,
            'sched_start_field_id': self.field('x_start').id, 'sched_start_mode': 'fixed', 'sched_start_date': T0})
        kpi = self.solve(problem).kpi_ids
        self.assertFalse(kpi.has_baseline, "no current dates: nothing to compare")
        self.assertEqual(kpi.after, 2.0)
        items[0].x_start = T0
        items[1].x_start = D(2026, 1, 5, 10)  # current plan ends at 12:00
        kpi = self.solve(problem).kpi_ids
        self.assertEqual((kpi.before, kpi.after, kpi.improved), (4.0, 2.0, True))

    def test_routing_km(self):
        depot = self.env['res.partner'].create({'name': 'Depot', 'partner_latitude': 24.63, 'partner_longitude': 46.71})
        north = self.env['res.partner'].create([{'name': 'N%s' % i, 'partner_latitude': 24.63 + 0.05 * i,
                                                 'partner_longitude': 46.71} for i in (1, 2)])
        items = self.Item.create([{'x_name': p.name, 'x_partner_id': p.id} for p in north])
        problem = self.env['or.problem'].create({
            'name': 'R', 'problem_type': 'routing', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % items.ids, 'route_partner_field_id': self.field('x_partner_id').id,
            'route_depot_id': depot.id, 'route_vehicle_field_id': self.field('x_vehicle_id').id,
            'route_sequence_field_id': self.field('x_seq').id, 'time_limit': 1,
            'route_vehicle_ids': [(0, 0, {'name': 'A', 'target_ref': 'x_or_machine,%s' % self.m1.id}),
                                  (0, 0, {'name': 'B', 'target_ref': 'x_or_machine,%s' % self.m2.id})]})
        items[0].write({'x_vehicle_id': self.m1.id, 'x_seq': 1})
        items[1].write({'x_vehicle_id': self.m2.id, 'x_seq': 1})  # two separate trips now
        run = self.solve(problem)
        km = run.kpi_ids.filtered(lambda k: k.name == 'Kilometres')
        self.assertTrue(km.has_baseline)
        self.assertLess(km.after, km.before, "one trip visiting both is shorter")
        served = run.kpi_ids.filtered(lambda k: k.name == 'Stops served')
        self.assertEqual((served.before, served.after), (2, 2))


@tagged('post_install', '-at_install', 'operations_research')
class TestPartialApplyAndUndo(OrCase):

    def test_apply_selected_then_rest(self):
        items, problem = self.wyndor()
        run = self.solve(problem)
        line_a = run.line_ids.filtered(lambda l: l.res_name == 'A')
        line_a.with_user(self.manager).action_apply_selected()
        self.assertEqual(items.mapped('x_qty'), [2.0, 0.0])
        self.assertEqual((run.state, run.applied_count), ('done', 1))
        with self.assertRaises(UserError):
            run.action_apply(line_ids=line_a.ids)
        run.action_apply()
        self.assertEqual((run.state, items.mapped('x_qty')), ('applied', [2.0, 6.0]))

    def test_undo_restores_previous_values(self):
        items, problem = self.wyndor()
        items.write({'x_qty': 1})
        run = self.solve(problem)
        run.action_apply()
        run.with_user(self.manager).action_undo_apply()
        self.assertEqual(items.mapped('x_qty'), [1.0, 1.0])
        self.assertEqual((run.state, run.applied_count), ('done', 0))
        run.action_apply()
        self.assertEqual(items.mapped('x_qty'), [2.0, 6.0], "can apply again after undo")

    def test_undo_protects_later_edits(self):
        items, problem = self.wyndor()
        run = self.solve(problem)
        run.action_apply()
        items[1].x_qty = 99
        with self.assertRaises(UserError):
            run.action_undo_apply()
        self.assertEqual(items.mapped('x_qty'), [2.0, 99.0])
        run.action_force_undo()
        self.assertEqual(items.mapped('x_qty'), [0.0, 0.0])

    def test_undo_selected_and_deleted_records(self):
        items, problem = self.wyndor()
        run = self.solve(problem)
        run.action_apply()
        run.line_ids.filtered(lambda l: l.res_name == 'A').action_undo_selected()
        self.assertEqual(items.mapped('x_qty'), [0.0, 6.0])
        items[1].unlink()
        run.action_undo_apply()
        self.assertIn('deleted record', run.message_ids[0].body)

    def test_undo_many2one_and_dates(self):
        tasks = self.Item.create([{'x_name': 't', 'x_duration': 1, 'x_machine_id': self.m1.id,
                                   'x_start': D(2026, 3, 1, 9)}])
        problem = self.env['or.problem'].create({
            'name': 'S', 'problem_type': 'scheduling', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % tasks.ids, 'sched_duration_field_id': self.field('x_duration').id,
            'sched_start_field_id': self.field('x_start').id, 'sched_end_field_id': self.field('x_end').id,
            'sched_start_mode': 'fixed', 'sched_start_date': T0})
        run = self.solve(problem)
        run.action_apply()
        self.assertEqual(tasks.x_start, T0)
        run.action_undo_apply()
        self.assertEqual((tasks.x_start, tasks.x_end), (D(2026, 3, 1, 9), False))

    def test_only_managers_undo(self):
        items, problem = self.wyndor()
        run = self.solve(problem)
        run.action_apply()
        for user in (self.user, self.designer):
            with self.assertRaises(AccessError):
                run.with_user(user).action_undo_apply()
            with self.assertRaises(AccessError):
                run.line_ids.with_user(user).action_apply_selected()
