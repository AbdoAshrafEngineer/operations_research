from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..tools import linear

from ..tools import routing


class OrProblem(models.Model):
    _inherit = 'or.problem'

    problem_type = fields.Selection(selection_add=[('routing', 'Vehicle Routing')],
                                    ondelete={'routing': 'cascade'})
    route_location_mode = fields.Selection(
        [('partner', 'Contact field (uses its geolocation)'), ('fields', 'Latitude / longitude fields')],
        string='Stop Location', default='partner', required=True)
    route_partner_field_id = fields.Many2one(
        'ir.model.fields', string='Contact Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'many2one'), ('relation', '=', 'res.partner')]")
    route_lat_field_id = fields.Many2one(
        'ir.model.fields', string='Latitude Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'float')]")
    route_lng_field_id = fields.Many2one(
        'ir.model.fields', string='Longitude Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'float')]")
    route_depot_id = fields.Many2one('res.partner', string='Depot', ondelete='restrict',
                                     help="Start and end of every route. Needs a geolocation.")
    route_vehicle_ids = fields.One2many('or.routing.vehicle', 'problem_id', string='Vehicles', copy=True)
    route_demand_field_id = fields.Many2one(
        'ir.model.fields', string='Demand Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer', 'monetary'))]")
    route_service_field_id = fields.Many2one(
        'ir.model.fields', string='Service Time Field (min)', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', 'in', ('float', 'integer'))]")
    route_tw_start_field_id = fields.Many2one(
        'ir.model.fields', string='Window Start Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'datetime')]")
    route_tw_end_field_id = fields.Many2one(
        'ir.model.fields', string='Window End Field', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'datetime')]")
    route_departure_mode = fields.Selection(
        [('now', 'When solving'), ('fixed', 'Fixed date')], string='Departure', default='now',
        required=True)
    route_departure_date = fields.Datetime(string='Departure Date')
    route_speed_kmh = fields.Float(
        string='Average Speed (km/h)',
        default=lambda self: self.env['ir.config_parameter'].sudo().get_float('operations_research.speed_kmh', 40.0) or 40.0)
    route_road_factor = fields.Float(
        string='Road Factor',
        default=lambda self: self.env['ir.config_parameter'].sudo().get_float('operations_research.road_factor', 1.3) or 1.3)
    route_allow_drop = fields.Boolean(string='Allow Skipping Stops',
                                      help="When capacity or time is short, skip stops instead of failing.")
    route_drop_penalty_km = fields.Float(string='Skip Penalty (km)', default=1000.0,
                                         help="A skipped stop costs as much as driving this many km.")
    route_balance = fields.Boolean(string='Balance Routes',
                                   help="Prefer routes of similar length over the shortest total.")
    route_vehicle_field_id = fields.Many2one(
        'ir.model.fields', string='Write Vehicle To', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'many2one')]")
    route_sequence_field_id = fields.Many2one(
        'ir.model.fields', string='Write Stop Order To', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'integer')]")
    route_eta_field_id = fields.Many2one(
        'ir.model.fields', string='Write Arrival Time To', ondelete='set null',
        domain="[('model_id', '=', model_id), ('ttype', '=', 'datetime')]")

    _route_speed_positive = models.Constraint('CHECK(route_speed_kmh IS NULL OR route_speed_kmh > 0)',
                                              'The average speed must be positive.')
    _route_factor_valid = models.Constraint('CHECK(route_road_factor IS NULL OR route_road_factor >= 1)',
                                            'The road factor must be at least 1.')

    def _or_model_field_names(self):
        return super()._or_model_field_names() + [
            'route_partner_field_id', 'route_lat_field_id', 'route_lng_field_id',
            'route_demand_field_id', 'route_service_field_id', 'route_tw_start_field_id',
            'route_tw_end_field_id', 'route_vehicle_field_id', 'route_sequence_field_id',
            'route_eta_field_id']

    def _or_signature_fields(self):
        return super()._or_signature_fields() + [
            'route_vehicle_field_id', 'route_sequence_field_id', 'route_eta_field_id']

    @api.constrains('route_vehicle_field_id', 'route_sequence_field_id', 'route_eta_field_id')
    def _check_route_write_fields(self):
        for problem in self:
            for field in (problem.route_vehicle_field_id, problem.route_sequence_field_id,
                          problem.route_eta_field_id):
                if field:
                    problem._check_writable_field(field)
            problem.route_vehicle_ids._check_target_ref()

    def _or_estimate_size_routing(self, count):
        return count * max(len(self.route_vehicle_ids.filtered('active')), 1)

    def _or_check_routing(self):
        if self.route_location_mode == 'partner' and not self.route_partner_field_id:
            raise UserError(self.env._("Choose the contact field giving each stop's location."))
        if self.route_location_mode == 'fields' and not (self.route_lat_field_id and self.route_lng_field_id):
            raise UserError(self.env._("Choose the latitude and longitude fields."))
        if not self.route_depot_id:
            raise UserError(self.env._("Choose the depot."))
        if not self.route_vehicle_ids.filtered('active'):
            raise UserError(self.env._("Add at least one vehicle."))
        if not (self.route_vehicle_field_id or self.route_sequence_field_id or self.route_eta_field_id):
            raise UserError(self.env._("Choose at least one field to write the routes to "
                                       "(vehicle, stop order or arrival time)."))
        if self.route_vehicle_field_id and any(not v.target_ref for v in self.route_vehicle_ids.filtered('active')):
            raise UserError(self.env._("Link every vehicle to a record to write the vehicle field."))
        if self.route_departure_mode == 'fixed' and not self.route_departure_date:
            raise UserError(self.env._("Choose the departure date."))

    @staticmethod
    def _or_has_location(lat, lng):
        return lat is not None and lng is not None and not (lat == 0 and lng == 0)

    def _or_route_location(self, record):
        if self.route_location_mode == 'partner':
            partner = record[self.route_partner_field_id.name]
            return partner.partner_latitude, partner.partner_longitude
        return record[self.route_lat_field_id.name], record[self.route_lng_field_id.name]

    def _or_solve_routing(self, run):
        records = self._or_get_records()
        vehicles = self.route_vehicle_ids.filtered('active')
        self._or_check_size(len(records) * len(vehicles))
        depot = self.route_depot_id
        if not self._or_has_location(depot.partner_latitude, depot.partner_longitude):
            raise UserError(self.env._("The depot %s has no geolocation.", depot.display_name))
        departure = (self.route_departure_date if self.route_departure_mode == 'fixed'
                     else fields.Datetime.now()).replace(microsecond=0)
        stops, missing, log = [], [], []
        for rec in records:
            lat, lng = self._or_route_location(rec)
            if not self._or_has_location(lat, lng):
                missing.append(rec.display_name)
                continue
            stop = {'key': rec.id, 'lat': lat, 'lng': lng,
                    'demand': self._or_num(rec, self.route_demand_field_id, 0.0),
                    'service': self._or_num(rec, self.route_service_field_id, 0.0) * 60,
                    'tw_start': None, 'tw_end': None}
            for key, field in (('tw_start', self.route_tw_start_field_id),
                               ('tw_end', self.route_tw_end_field_id)):
                if field and rec[field.name]:
                    stop[key] = int((rec[field.name] - departure).total_seconds())
            if stop['tw_end'] is not None and stop['tw_end'] < 0:
                log.append(self.env._("%s: delivery window already closed at departure.", rec.display_name))
            stops.append(stop)
        if missing:
            raise UserError(self.env._(
                "No geolocation for: %s. Geolocate the contacts (latitude/longitude) first.",
                ', '.join(missing[:10])))
        vehicle_data = [{'key': v.id, 'capacity': v.capacity,
                         'max_distance_m': int(v.max_distance_km * 1000)} for v in vehicles]
        result = routing.solve_vrp(
            (depot.partner_latitude, depot.partner_longitude), stops, vehicle_data,
            road_factor=self.route_road_factor or 1.3, speed_kmh=self.route_speed_kmh or 40.0,
            allow_drop=self.route_allow_drop, drop_penalty=int(self.route_drop_penalty_km * 1000),
            balance=self.route_balance, time_limit=self.time_limit,
            use_time=bool(self.route_eta_field_id))
        snapshot = self._or_snapshot_routing(records)
        by_id = {rec.id: rec for rec in records}
        lines = []
        if result['status'] in linear.SOLUTION_STATUSES:
            vehicle_by_id = {v.id: v for v in vehicles}
            for vehicle_id, route in result['routes'].items():
                vehicle = vehicle_by_id[vehicle_id]
                for sequence, rec_id in enumerate(route, 1):
                    rec = by_id[rec_id]
                    lines.append(self._or_route_line(rec, snapshot, vehicle, sequence,
                                                     departure + timedelta(seconds=result['eta'][rec_id]),
                                                     result['leg_km'][rec_id]))
            for rec_id in result['dropped']:
                lines.append(self._or_route_line(by_id[rec_id], snapshot, None, 0, False, 0.0))
            if result['dropped']:
                log.append(self.env._("Skipped stops: %s", ', '.join(
                    by_id[r].display_name for r in result['dropped'][:20])))
            for vehicle_id, km in result['vehicle_km'].items():
                log.append(self.env._("%(vehicle)s: %(stops)s stop(s), %(km)s km",
                                      vehicle=vehicle_by_id[vehicle_id].name,
                                      stops=len(result['routes'][vehicle_id]), km=round(km, 1)))
        elif result['status'] == linear.INFEASIBLE:
            log.append(self.env._("No route plan fits the capacities, distances and time windows. "
                                  "Add vehicles, relax windows, or allow skipping stops."))
        result.update({'lines': lines, 'constraint_lines': [], 'log': log,
                       'departure': departure})
        return result

    def _or_route_line(self, rec, snapshot, vehicle, sequence, eta, leg_km):
        before_vehicle = rec[self.route_vehicle_field_id.name] if self.route_vehicle_field_id else None
        return {
            'sequence': sequence,
            'res_model': self.model_name,
            'res_id': rec.id,
            'res_name': rec.display_name,
            'route_vehicle_id': vehicle.id if vehicle else False,
            'assigned_name': vehicle.name if vehicle else False,
            'assigned_res_id': vehicle.target_ref.id if vehicle and vehicle.target_ref else 0,
            'assigned_before_name': before_vehicle.display_name if before_vehicle else False,
            'value_before': (float(rec[self.route_sequence_field_id.name] or 0)
                             if self.route_sequence_field_id else 0.0),
            'value': float(sequence),
            'route_eta': eta,
            'route_leg_km': leg_km,
            'route_dropped': vehicle is None,
            'route_stop_name': (rec[self.route_partner_field_id.name].display_name
                                if self.route_location_mode == 'partner' and self.route_partner_field_id
                                else rec.display_name),
            'snapshot': snapshot[rec.id],
        }

    def _or_snapshot_routing(self, records):
        names = [f.name for f in (self.route_vehicle_field_id, self.route_sequence_field_id,
                                  self.route_eta_field_id) if f]
        result = {}
        for rec in records:
            parts = []
            for name in names:
                value = rec[name]
                parts.append(str(value.id if isinstance(value, models.BaseModel) else value or ''))
            result[rec.id] = '|'.join(parts)
        return result

    def _or_apply_routing(self, run, lines):
        Model = self._or_get_model()
        for line in lines:
            if line.route_dropped:
                vals = {}
                if self.route_vehicle_field_id and not Model._fields[self.route_vehicle_field_id.name].required:
                    vals[self.route_vehicle_field_id.name] = False
                if self.route_sequence_field_id:
                    vals[self.route_sequence_field_id.name] = 0
                if self.route_eta_field_id:
                    vals[self.route_eta_field_id.name] = False
            else:
                vals = {}
                if self.route_vehicle_field_id:
                    vals[self.route_vehicle_field_id.name] = line.route_vehicle_id.target_ref.id
                if self.route_sequence_field_id:
                    vals[self.route_sequence_field_id.name] = int(line.value)
                if self.route_eta_field_id:
                    vals[self.route_eta_field_id.name] = line.route_eta
            if vals:
                Model.browse(line.res_id).write(vals)
