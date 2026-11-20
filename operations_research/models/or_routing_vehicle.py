from odoo import api, fields, models
from odoo.exceptions import ValidationError


class OrRoutingVehicle(models.Model):
    _name = 'or.routing.vehicle'
    _description = 'Routing Vehicle'
    _order = 'sequence, id'

    problem_id = fields.Many2one('or.problem', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='problem_id.company_id', store=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    name = fields.Char(required=True, copy=True)
    capacity = fields.Float(help="Maximum total demand. 0 = unlimited.")
    max_distance_km = fields.Float(string='Max Distance (km)', help="0 = unlimited.")
    target_ref = fields.Reference(
        selection='_selection_target_model', string='Linked Record',
        help="Record written to the problem's vehicle field for the stops of this vehicle "
             "(e.g. a driver or a fleet vehicle).")

    _capacity_positive = models.Constraint('CHECK(capacity >= 0)', 'Capacity cannot be negative.')
    _distance_positive = models.Constraint('CHECK(max_distance_km >= 0)',
                                           'Maximum distance cannot be negative.')

    @api.model
    def _selection_target_model(self):
        return [(model.model, model.name) for model in self.env['ir.model'].sudo().search(
            [('transient', '=', False), ('abstract', '=', False)])]

    @api.constrains('target_ref', 'problem_id')
    def _check_target_ref(self):
        for vehicle in self:
            relation = vehicle.problem_id.route_vehicle_field_id.relation
            if vehicle.target_ref and relation and vehicle.target_ref._name != relation:
                raise ValidationError(self.env._(
                    "Vehicle %(vehicle)s must be linked to a %(model)s record.",
                    vehicle=vehicle.name, model=relation))
