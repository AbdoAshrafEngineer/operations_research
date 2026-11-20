from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    or_route_speed_kmh = fields.Integer(string='Average Speed (km/h)', default=40,
                                        config_parameter='operations_research.speed_kmh')
    or_route_road_factor = fields.Float(string='Road Factor', default=1.3,
                                        config_parameter='operations_research.road_factor',
                                        help="Road distance / straight-line distance. 1.3 is typical in cities.")
