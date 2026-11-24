from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import T0, OrCase


@tagged('post_install', '-at_install', 'operations_research')
class TestLinearPrograms(OrCase):

    def test_solve_review_apply(self):
        items, problem = self.wyndor()
        run = self.solve(problem)
        self.assertEqual((run.state, run.solver_status), ('done', 'optimal'))
        self.assertAlmostEqual(run.objective_value, 36.0)
        self.assertEqual({line.res_name: line.value for line in run.line_ids}, {'A': 2.0, 'B': 6.0})
        duals = {c.name: c.dual_value for c in run.constraint_result_ids}
        self.assertAlmostEqual(duals['Plant 2'], 1.5)
        self.assertAlmostEqual(duals['Plant 3'], 1.0)
        self.assertCountEqual(run.constraint_result_ids.filtered('is_binding').mapped('name'), ['Plant 2', 'Plant 3'])
        self.assertEqual(items.mapped('x_qty'), [0.0, 0.0], "nothing is written before Apply")
        run.action_apply()
        self.assertEqual((run.state, items.mapped('x_qty')), ('applied', [2.0, 6.0]))
        with self.assertRaises(UserError):
            run.action_apply()

    def test_integer_boolean_and_bounds(self):
        items = self.Item.create([{'x_name': 'i1', 'x_weight': 5, 'x_price': 10},
                                  {'x_name': 'i2', 'x_weight': 4, 'x_price': 40},
                                  {'x_name': 'i3', 'x_weight': 3, 'x_price': 30}])
        knapsack = self.lp(items, [{'name': 'Cap', 'coef_field_id': 'x_weight', 'rhs_value': 7}],
                           var_field_id=self.field('x_selected').id, var_type='binary')
        run = self.solve(knapsack)
        self.assertAlmostEqual(run.objective_value, 70.0)
        run.action_apply()
        self.assertEqual(items.mapped('x_selected'), [False, True, True])
        items.write({'x_min': 1, 'x_max': 2})
        bounded = self.lp(items, var_field_id=self.field('x_qty_int').id, var_type='integer',
                          lb_field_id=self.field('x_min').id, ub_field_id=self.field('x_max').id)
        run = self.solve(bounded)
        run.action_apply()
        self.assertEqual(items.mapped('x_qty_int'), [2, 2, 2])

    def test_group_by_machine_capacity(self):
        items = self.Item.create([
            {'x_name': 'a', 'x_price': 4, 'x_hours': 1, 'x_machine_id': self.m1.id},
            {'x_name': 'b', 'x_price': 1, 'x_hours': 1, 'x_machine_id': self.m1.id},
            {'x_name': 'c', 'x_price': 2, 'x_hours': 2, 'x_machine_id': self.m2.id},
            {'x_name': 'free', 'x_price': 1, 'x_hours': 1},
        ])
        problem = self.lp(items, [{'name': 'Hours', 'coef_field_id': 'x_hours', 'group_by_field_id': 'x_machine_id',
                                   'rhs_mode': 'group_field', 'rhs_field_id': 'x_capacity'}],
                          ub_infinite=False, ub_value=100)
        run = self.solve(problem)
        self.assertEqual({line.res_name: line.value for line in run.line_ids},
                         {'a': 10.0, 'b': 0.0, 'c': 3.0, 'free': 100.0})
        self.assertIn('skipped', run.log)

    def test_infeasible_unbounded_and_errors(self):
        items = self.Item.create([{'x_name': 'a', 'x_price': 1, 'x_load': 1}])
        problem = self.lp(items, [
            {'name': 'At least 5', 'coef_field_id': 'x_load', 'operator': '>=', 'rhs_value': 5},
            {'name': 'At most 3', 'coef_field_id': 'x_load', 'rhs_value': 3}])
        run = self.solve(problem)
        self.assertEqual(run.solver_status, 'infeasible')
        self.assertCountEqual(run.conflict_ids.mapped('name'), ['At least 5', 'At most 3'])
        self.assertEqual(self.solve(self.lp(items)).solver_status, 'unbounded')
        with self.assertRaises(UserError):
            self.lp(self.Item, domain="[('id', '=', 0)]").action_run()
        with self.assertRaises(ValidationError):
            self.lp(items, domain="[('id', '=='")
        self.env['ir.config_parameter'].set_param('operations_research.max_variables', 0)
        with self.assertRaises(UserError):
            self.lp(items).action_run()

    def test_values_through_links(self):
        """Objective and constraint values can follow many2one links (e.g. product > cost)."""
        items = self.Item.create([{'x_name': 'on M1', 'x_machine_id': self.m1.id},
                                  {'x_name': 'on M2', 'x_machine_id': self.m2.id}])
        problem = self.lp(items, [{'name': 'Budget', 'coef_path': 'x_machine_id.x_rate', 'rhs_value': 30}],
                          objective_field_id=False, objective_path='x_machine_id.x_capacity')
        run = self.solve(problem)
        # value per unit 10 (M1, cost 5) vs 6 (M2, cost 3): 2.0 vs 2.0 per cost unit -> objective 60
        self.assertAlmostEqual(run.objective_value, 60.0)
        self.assertIn('Machine > Rate', problem.constraint_ids.summary)
        with self.assertRaises(ValidationError):
            problem.objective_path = 'x_machine_id.x_name'
        with self.assertRaises(ValidationError):
            problem.objective_path = 'x_nope'
        items[1].x_machine_id = False
        self.assertEqual(problem._or_path_num(items[1], 'x_machine_id.x_rate', 7.0), 7.0)

    def test_conflicts_and_force_apply(self):
        items, problem = self.wyndor()
        run = self.solve(problem)
        items[0].x_qty = 99
        with self.assertRaisesRegex(UserError, 'changed or deleted'):
            run.action_apply()
        run.action_force_apply()
        self.assertEqual(items.mapped('x_qty'), [2.0, 6.0])
        run2 = self.solve(problem)
        problem.var_field_id = self.field('x_price')
        with self.assertRaisesRegex(UserError, 'configuration changed'):
            run2.action_apply()


@tagged('post_install', '-at_install', 'operations_research')
class TestOtherProblemTypes(OrCase):

    def test_assignment(self):
        m3 = self.Machine.create({'x_name': 'M3', 'x_capacity': 1, 'x_rate': 1, 'x_skill': 'cut'})
        tasks = self.Item.create([{'x_name': 'Weld job', 'x_skill': 'weld', 'x_hours': 2},
                                  {'x_name': 'Cut job', 'x_skill': 'cut', 'x_hours': 2}])
        problem = self.env['or.problem'].create({
            'name': 'Assign', 'problem_type': 'assignment', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % tasks.ids, 'assign_field_id': self.field('x_assigned_id').id,
            'assign_domain': "[('id', 'in', %s)]" % (self.m1 | self.m2 | m3).ids, 'objective_sense': 'min',
            'assign_cost_mode': 'product', 'assign_task_weight_field_id': self.field('x_hours').id,
            'assign_resource_cost_field_id': self.field('x_rate', 'x_or_machine').id,
            'assign_task_match_field_id': self.field('x_skill').id,
            'assign_resource_match_field_id': self.field('x_skill', 'x_or_machine').id,
            'assign_mismatch_policy': 'forbid'})
        run = self.solve(problem)
        self.assertEqual({line.res_name: line.assigned_name for line in run.line_ids},
                         {'Weld job': 'M2', 'Cut job': 'M3'})
        run.action_apply()
        self.assertEqual(tasks.mapped('x_assigned_id.x_name'), ['M2', 'M3'])

    def test_scheduling_and_gantt(self):
        items = self.Item.create([{'x_name': 'Cut', 'x_duration': 2, 'x_machine_id': self.m1.id},
                                  {'x_name': 'Weld', 'x_duration': 1, 'x_machine_id': self.m2.id}])
        items[1].x_pred_ids = items[0]
        problem = self.env['or.problem'].create({
            'name': 'Plan', 'problem_type': 'scheduling', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % items.ids, 'sched_duration_field_id': self.field('x_duration').id,
            'sched_resource_field_id': self.field('x_machine_id').id,
            'sched_predecessor_field_id': self.field('x_pred_ids').id,
            'sched_start_field_id': self.field('x_start').id, 'sched_end_field_id': self.field('x_end').id,
            'sched_start_mode': 'fixed', 'sched_start_date': T0})
        run = self.solve(problem)
        self.assertAlmostEqual(run.objective_value, 3.0)
        run.action_apply()
        self.assertEqual(items[1].x_start, items[0].x_end)
        self.assertEqual(items[0].x_start, T0)
        self.assertEqual(len(run.get_gantt_data()['rows']), 2)
        items[0].x_pred_ids = items[1]
        self.assertIn('Circular', self.solve(problem).error_message)

    def test_routing_and_map(self):
        depot = self.env['res.partner'].create({'name': 'Depot', 'partner_latitude': 24.63, 'partner_longitude': 46.71})
        customers = self.env['res.partner'].create([
            {'name': 'C%s' % i, 'partner_latitude': 24.63 + 0.02 * i, 'partner_longitude': 46.71} for i in range(1, 5)])
        items = self.Item.create([{'x_name': c.name, 'x_partner_id': c.id, 'x_load': 1} for c in customers])
        problem = self.env['or.problem'].create({
            'name': 'Routes', 'problem_type': 'routing', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % items.ids, 'route_partner_field_id': self.field('x_partner_id').id,
            'route_depot_id': depot.id, 'route_demand_field_id': self.field('x_load').id,
            'route_vehicle_field_id': self.field('x_vehicle_id').id, 'route_sequence_field_id': self.field('x_seq').id,
            'route_eta_field_id': self.field('x_eta').id, 'route_departure_mode': 'fixed',
            'route_departure_date': T0, 'time_limit': 1,
            'route_vehicle_ids': [(0, 0, {'name': 'Van A', 'capacity': 2, 'target_ref': 'x_or_machine,%s' % self.m1.id}),
                                  (0, 0, {'name': 'Van B', 'capacity': 2, 'target_ref': 'x_or_machine,%s' % self.m2.id})]})
        run = self.solve(problem)
        self.assertIn(run.solver_status, ('optimal', 'feasible'))
        self.assertEqual(sorted(run.line_ids.mapped('route_stop_name')), ['C1', 'C2', 'C3', 'C4'])
        run.action_apply()
        self.assertEqual(len(items.mapped('x_vehicle_id')), 2)
        self.assertTrue(all(items.mapped('x_eta')))
        data = run.get_route_map_data()
        self.assertEqual(sum(len(r['stops']) for r in data['routes']), 4)
        problem.route_vehicle_ids[1].active = False
        self.assertIn('Total load', self.solve(problem).error_message, "explained before solving")
        problem.route_allow_drop = True
        self.assertEqual(len(self.solve(problem).line_ids.filtered('route_dropped')), 2)


@tagged('post_install', '-at_install', 'operations_research')
class TestLifecycleAndSecurity(OrCase):

    def test_roles_and_cold_cache(self):
        items, problem = self.wyndor()
        self.env.invalidate_all()
        run = self.solve(problem, user=self.user)
        self.assertEqual(run.state, 'done', run.error_message)
        with self.assertRaises(AccessError):
            run.with_user(self.user).action_apply()
        with self.assertRaises(AccessError):
            problem.with_user(self.user).write({'name': 'x'})
        with self.assertRaises(AccessError):
            problem.with_user(self.outsider).read(['name'])
        with self.assertRaises(AccessError):
            problem.with_user(self.designer).write({'schedule_auto_apply': True})
        self.env.invalidate_all()
        run.with_user(self.manager).action_apply()
        self.assertEqual(items.mapped('x_qty'), [2.0, 6.0])

    def test_queue_schedule_and_auto_apply(self):
        items, problem = self.wyndor()
        queued = self.env['or.run'].browse(problem.action_queue()['res_id'])
        self.assertEqual(queued.state, 'queued')
        self.env['or.run']._cron_process_queue()
        self.assertEqual(queued.state, 'done')
        self.assertTrue(queued.message_ids.filtered(lambda m: 'finished' in (m.body or '')))
        problem.write({'schedule_active': True, 'schedule_next_date': fields.Datetime.now() - timedelta(days=2),
                       'schedule_user_id': self.manager.id, 'schedule_auto_apply': True})
        scheduled = self.env['or.problem']._cron_run_scheduled()
        self.assertGreater(problem.schedule_next_date, fields.Datetime.now())
        self.env['or.run']._cron_process_queue()
        self.assertEqual(scheduled.state, 'applied')
        self.assertEqual(items.mapped('x_qty'), [2.0, 6.0])
        stuck = problem._or_new_run()
        stuck.write({'state': 'running', 'date_started': fields.Datetime.now() - timedelta(hours=5)})
        self.env['or.run']._cron_process_queue()
        self.assertEqual(stuck.state, 'failed')

    def test_scenarios_and_dashboard(self):
        _items, problem = self.wyndor()
        scenario = self.env['or.problem'].browse(problem.action_create_scenario()['res_id'])
        self.assertEqual((scenario.parent_id, len(scenario.constraint_ids)), (problem, 3))
        scenario.constraint_ids.filtered(lambda c: c.name == 'Plant 3').rhs_value = 24
        base, what_if = self.solve(problem), self.solve(scenario)
        self.assertGreater(what_if.objective_value, base.objective_value)
        self.assertEqual(self.env['or.run'].search(problem.action_compare_scenarios()['domain']), base | what_if)
        data = self.env['or.run'].get_or_dashboard_data(days=7)
        self.assertEqual(data['kpis']['to_apply'], 2)
        self.assertEqual(data['kpis']['success_rate'], 100.0)

    def test_arabic(self):
        self.env['res.lang']._activate_lang('ar_001')
        self.env['ir.module.module']._load_module_terms(['operations_research'], ['ar_001'], overwrite=True)
        fields_ar = self.env['or.problem'].with_context(lang='ar_001').fields_get(['problem_type', 'last_run_status'])
        self.assertEqual(fields_ar['problem_type']['string'], 'نوع المسألة')
        self.assertIn(('routing', 'توجيه المركبات'), fields_ar['problem_type']['selection'])
        self.assertIn(('optimal', 'أمثل'), fields_ar['last_run_status']['selection'])
