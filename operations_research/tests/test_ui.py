from odoo.tests import HttpCase, tagged

from .common import T0, OrCase


@tagged('post_install', '-at_install', 'operations_research', 'operations_research_ui')
class TestUi(HttpCase, OrCase):

    def test_tutorials_and_guided_tour(self):
        self.wyndor()
        self.start_tour('/odoo/action-operations_research.or_tutorials_action', 'or_tutorials_tour', login='admin')

    def test_dashboard(self):
        _items, problem = self.wyndor()
        self.solve(problem)
        self.start_tour('/odoo/action-operations_research.or_dashboard_action', 'or_dashboard_tour', login='admin')

    def test_gantt(self):
        items = self.Item.create([{'x_name': 'J%s' % i, 'x_duration': 1 + i, 'x_machine_id': self.m1.id}
                                  for i in range(3)])
        problem = self.env['or.problem'].create({
            'name': 'Plan', 'problem_type': 'scheduling', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % items.ids, 'sched_duration_field_id': self.field('x_duration').id,
            'sched_resource_field_id': self.field('x_machine_id').id, 'sched_start_field_id': self.field('x_start').id,
            'sched_start_mode': 'fixed', 'sched_start_date': T0})
        run = self.solve(problem)
        self.start_tour('/odoo/action-operations_research.or_run_action/%s' % run.id, 'or_gantt_map_tour', login='admin')

    def test_map(self):
        depot = self.env['res.partner'].create({'name': 'Depot', 'partner_latitude': 24.6, 'partner_longitude': 46.7})
        items = self.Item.create([{'x_name': 'S%s' % i, 'x_partner_id': self.env['res.partner'].create(
            {'name': 'C%s' % i, 'partner_latitude': 24.6 + 0.01 * i, 'partner_longitude': 46.7 + 0.01 * i}).id}
            for i in range(1, 4)])
        problem = self.env['or.problem'].create({
            'name': 'Routes', 'problem_type': 'routing', 'model_id': self.item_model.id,
            'domain': "[('id', 'in', %s)]" % items.ids, 'route_partner_field_id': self.field('x_partner_id').id,
            'route_depot_id': depot.id, 'route_sequence_field_id': self.field('x_seq').id, 'time_limit': 1,
            'route_vehicle_ids': [(0, 0, {'name': 'Van'})]})
        run = self.solve(problem)
        self.start_tour('/odoo/action-operations_research.or_run_action/%s' % run.id, 'or_map_tour', login='admin')
