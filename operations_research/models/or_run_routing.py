from odoo import api, fields, models

from ..tools.routing import haversine_km

PALETTE = ['#2E86AB', '#E4572E', '#29A36A', '#A23B72', '#F3A712', '#4F5D75', '#00A6A6', '#8E6C8A']


class OrRun(models.Model):
    _inherit = 'or.run'

    def action_open_route_map(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'operations_research.map',
            'name': self.env._("Routes %s", self.name),
            'params': {'run_id': self.id},
        }

    def get_route_map_data(self):
        """Data for the offline Owl route map (no external tiles)."""
        self.ensure_one()
        self.check_access('read')
        problem = self.problem_id
        depot = problem.route_depot_id
        Model = self.env[problem.model_name] if problem.model_name in self.env else None
        records = Model.browse(self.line_ids.mapped('res_id')).exists() if Model is not None else []
        locations = {rec.id: problem._or_route_location(rec) for rec in records}
        routes = {}
        dropped = []
        for line in self.line_ids.sorted(lambda line: (line.route_vehicle_id.id, line.sequence)):
            lat, lng = locations.get(line.res_id, (None, None))
            if lat is None:
                continue
            stop = {'id': line.id, 'res_id': line.res_id, 'res_model': line.res_model,
                    'name': line.route_stop_name or line.res_name, 'lat': lat, 'lng': lng, 'sequence': line.sequence,
                    'eta': fields.Datetime.to_string(line.route_eta) if line.route_eta else False,
                    'leg_km': line.route_leg_km}
            if line.route_dropped:
                dropped.append(stop)
                continue
            vehicle = line.route_vehicle_id
            route = routes.setdefault(vehicle.id, {
                'id': vehicle.id, 'name': vehicle.name, 'stops': [], 'km': 0.0,
                'color': PALETTE[len(routes) % len(PALETTE)]})
            route['stops'].append(stop)
            route['km'] += line.route_leg_km
        for route in routes.values():
            if route['stops']:
                last = route['stops'][-1]
                route['km'] += haversine_km(last['lat'], last['lng'], depot.partner_latitude,
                                            depot.partner_longitude) * (problem.route_road_factor or 1.3)
            route['km'] = round(route['km'], 1)
        return {
            'run': {'id': self.id, 'name': self.name, 'problem': problem.name,
                    'objective': self.objective_value, 'status': self.solver_status},
            'depot': {'name': depot.display_name, 'lat': depot.partner_latitude, 'lng': depot.partner_longitude},
            'routes': list(routes.values()),
            'dropped': dropped,
        }


class OrRunLine(models.Model):
    _inherit = 'or.run.line'

    route_vehicle_id = fields.Many2one('or.routing.vehicle', string='Vehicle', ondelete='set null')
    route_eta = fields.Datetime(string='Arrival')
    route_leg_km = fields.Float(string='Leg (km)')
    route_dropped = fields.Boolean(string='Skipped')
    route_stop_name = fields.Char(string='Stop', help="Customer or place of this stop.")

    @api.depends('route_vehicle_id', 'route_dropped', 'run_id.problem_type')
    def _compute_delta(self):
        super()._compute_delta()
        for line in self:
            if line.run_id.problem_type == 'routing':
                line.changed = line.changed or line.assigned_name != line.assigned_before_name \
                    or line.route_dropped
