from datetime import datetime as D
from unittest.mock import patch

from odoo.tests import tagged

from .common import OrCase

MONDAY_8 = D(2026, 1, 5, 8, 0)


@tagged('post_install', '-at_install', 'operations_research')
class TestWorkingHours(OrCase):

    def plan(self, durations, **vals):
        items = self.Item.create([{'x_name': name, 'x_duration': hours, 'x_machine_id': machine.id}
                                  for name, hours, machine in durations])
        values = {
            'name': 'Plan', 'problem_type': 'scheduling', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % items.ids, 'sched_duration_field_id': self.field('x_duration').id,
            'sched_resource_field_id': self.field('x_machine_id').id,
            'sched_predecessor_field_id': self.field('x_pred_ids').id,
            'sched_start_field_id': self.field('x_start').id, 'sched_end_field_id': self.field('x_end').id,
            'sched_start_mode': 'fixed', 'sched_start_date': MONDAY_8, 'time_limit': 5}
        values.update(vals)
        return items, self.env['or.problem'].create(values)

    def test_jobs_pause_outside_working_hours(self):
        items, problem = self.plan([('Cut', 6, self.m1), ('Weld', 4, self.m2)],
                                   sched_time_mode='calendar', sched_calendar_id=self.make_calendar().id)
        items[1].x_pred_ids = items[0]
        run = self.solve(problem)
        self.assertEqual(run.solver_status, 'optimal')
        run.action_apply()
        self.assertEqual((items[0].x_start, items[0].x_end), (MONDAY_8, D(2026, 1, 5, 15)),
                         "4h before lunch + 2h after")
        self.assertEqual((items[1].x_start, items[1].x_end), (D(2026, 1, 5, 15), D(2026, 1, 6, 10)),
                         "continues next morning")

    def test_working_hours_are_local_time(self):
        calendar = self.make_calendar()
        calendar.tz = 'Asia/Riyadh'
        items, problem = self.plan([('Job', 1, self.m1)], sched_time_mode='calendar',
                                   sched_calendar_id=calendar.id, sched_start_date=D(2026, 1, 5, 4))
        self.solve(problem).action_apply()
        self.assertEqual((items.x_start, items.x_end), (D(2026, 1, 5, 5), D(2026, 1, 5, 6)),
                         "08:00 in Riyadh is 05:00 UTC")

    def test_around_the_clock_unchanged(self):
        items, problem = self.plan([('Cut', 6, self.m1), ('Weld', 4, self.m2)])
        items[1].x_pred_ids = items[0]
        self.solve(problem).action_apply()
        self.assertEqual(items[1].x_end, D(2026, 1, 5, 18))

    def test_weekend_and_holiday_skipped(self):
        calendar = self.make_calendar(holidays=[D(2026, 1, 12)])  # next Monday closed
        items, problem = self.plan([('Big job', 12, self.m1)], sched_time_mode='calendar',
                                   sched_calendar_id=calendar.id, sched_start_date=D(2026, 1, 9, 13))
        self.solve(problem).action_apply()
        self.assertEqual(items.x_start, D(2026, 1, 9, 13), "Friday afternoon")
        self.assertEqual(items.x_end, D(2026, 1, 13, 17), "skips the weekend and the closed Monday")

    def test_start_outside_hours_snaps_to_opening(self):
        items, problem = self.plan([('Job', 1, self.m1)], sched_time_mode='calendar',
                                   sched_calendar_id=self.make_calendar().id, sched_start_date=D(2026, 1, 3, 22))
        self.solve(problem).action_apply()
        self.assertEqual((items.x_start, items.x_end), (MONDAY_8, D(2026, 1, 5, 9)))

    def test_days_are_working_days(self):
        items, problem = self.plan([('Two days', 2, self.m1)], sched_time_mode='calendar',
                                   sched_calendar_id=self.make_calendar().id, sched_duration_unit='days')
        self.solve(problem).action_apply()
        self.assertEqual(items.x_end, D(2026, 1, 6, 17))

    def test_deadline_and_lateness_in_real_time(self):
        items, problem = self.plan([('Job', 10, self.m1)], sched_time_mode='calendar',
                                   sched_calendar_id=self.make_calendar().id,
                                   sched_deadline_field_id=self.field('x_deadline').id,
                                   sched_objective='tardiness')
        items.x_deadline = D(2026, 1, 5, 17)
        run = self.solve(problem)
        self.assertEqual(run.line_ids.sched_end, D(2026, 1, 6, 10))
        self.assertEqual(run.line_ids.sched_late_minutes, 17 * 60, "17:00 Monday -> 10:00 Tuesday")
        problem.sched_deadline_mode = 'hard'
        run = self.solve(problem)
        self.assertEqual(run.solver_status, 'infeasible')
        self.assertIn('Deadline earlier', run.log)

    def test_calendar_without_hours_explains(self):
        calendar = self.env['resource.calendar'].create({'name': 'Never', 'tz': 'UTC', 'attendance_ids': [(5, 0, 0)]})
        _items, problem = self.plan([('Job', 1, self.m1)], sched_time_mode='calendar',
                                    sched_calendar_id=calendar.id)
        run = self.solve(problem)
        self.assertEqual(run.state, 'failed')
        self.assertIn('too little time', run.error_message)

    def test_negative_weight_explained(self):
        items, problem = self.plan([('Job', 1, self.m1)], sched_weight_field_id=self.field('x_weight').id)
        items.x_weight = -1
        self.assertIn('Negative priority', self.solve(problem).error_message)


@tagged('post_install', '-at_install', 'operations_research')
class TestRoutingChecks(OrCase):

    def routes(self, loads, capacities, **vals):
        depot = self.env['res.partner'].create({'name': 'Depot', 'partner_latitude': 24.63, 'partner_longitude': 46.71})
        customers = self.env['res.partner'].create([
            {'name': 'C%s' % i, 'partner_latitude': 24.63 + 0.02 * i, 'partner_longitude': 46.71}
            for i in range(1, len(loads) + 1)])
        items = self.Item.create([{'x_name': c.name, 'x_partner_id': c.id, 'x_load': load}
                                  for c, load in zip(customers, loads)])
        values = {
            'name': 'Routes', 'problem_type': 'routing', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % items.ids, 'route_partner_field_id': self.field('x_partner_id').id,
            'route_depot_id': depot.id, 'route_demand_field_id': self.field('x_load').id,
            'route_vehicle_field_id': self.field('x_vehicle_id').id, 'time_limit': 1,
            'route_vehicle_ids': [(0, 0, {'name': 'Van %s' % i, 'capacity': cap,
                                          'target_ref': 'x_or_machine,%s' % self.m1.id})
                                  for i, cap in enumerate(capacities)]}
        values.update(vals)
        return items, self.env['or.problem'].create(values)

    def test_decimal_loads(self):
        _items, problem = self.routes([0.4] * 5, [1.0, 1.0, 1.0])
        run = self.solve(problem)
        self.assertIn(run.solver_status, ('optimal', 'feasible'))
        per_van = {}
        for line in run.line_ids:
            per_van[line.route_vehicle_id] = per_van.get(line.route_vehicle_id, 0) + 0.4
        self.assertTrue(all(load <= 1.0 + 1e-9 for load in per_van.values()), per_van)

    def test_stop_bigger_than_any_vehicle(self):
        _items, problem = self.routes([1, 7], [5, 5])
        run = self.solve(problem)
        self.assertEqual(run.state, 'failed')
        self.assertIn('C2', run.error_message)
        problem.route_allow_drop = True
        run = self.solve(problem)
        self.assertEqual(run.line_ids.filtered('route_dropped').mapped('route_stop_name'), ['C2'])

    def test_total_load_too_big(self):
        _items, problem = self.routes([3, 3, 3], [4, 4])
        run = self.solve(problem)
        self.assertIn('Total load 9', run.error_message)

    def test_negative_load(self):
        _items, problem = self.routes([1, -2], [5])
        self.assertIn('cannot be negative', self.solve(problem).error_message)

    def test_unlimited_vehicle_skips_capacity_checks(self):
        _items, problem = self.routes([100, 3], [5, 0])
        self.assertIn(self.solve(problem).solver_status, ('optimal', 'feasible'))

    def test_infeasible_names_the_stops(self):
        _items, problem = self.routes([1, 1], [5], route_eta_field_id=self.field('x_eta').id,
                                      route_tw_end_field_id=self.field('x_deadline').id,
                                      route_departure_mode='fixed', route_departure_date=MONDAY_8)
        _items[1].x_deadline = D(2026, 1, 5, 8, 1)  # far stop, one minute after departure
        run = self.solve(problem)
        self.assertEqual(run.solver_status, 'infeasible')
        self.assertIn('cannot fit: C2', run.log)


@tagged('post_install', '-at_install', 'operations_research')
class TestServerTimeLimits(OrCase):

    def test_long_solves_go_to_background(self):
        _items, problem = self.wyndor()
        cfg = {'workers': 4, 'limit_time_real': 120, 'limit_time_real_cron': -1}
        with patch.dict('odoo.addons.operations_research.models.or_problem.config.options', cfg):
            problem.time_limit = 30
            self.assertTrue(problem._or_can_solve_now())
            problem.time_limit = 300
            self.assertFalse(problem._or_can_solve_now())
            self.assertEqual(problem._or_effective_time_limit(), 36, "capped under the kill limit")
            run = self.solve(problem)
            self.assertEqual(run.state, 'queued')

    def test_threaded_server_has_no_cap(self):
        _items, problem = self.wyndor()
        with patch.dict('odoo.addons.operations_research.models.or_problem.config.options', {'workers': 0}):
            problem.time_limit = 300
            self.assertEqual(problem._or_effective_time_limit(), 300)

    def test_solver_version_stored(self):
        _items, problem = self.wyndor()
        self.assertTrue(self.solve(problem).solver_version)
