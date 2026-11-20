from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    or_time_limit = fields.Integer(string='Default Time Limit (s)', default=30,
                                   config_parameter='operations_research.time_limit')
    or_num_workers = fields.Integer(string='Default Solver Threads', default=1,
                                    config_parameter='operations_research.num_workers')
    or_max_variables = fields.Integer(string='Maximum Variables', default=200000,
                                      config_parameter='operations_research.max_variables')
    or_sync_max_variables = fields.Integer(string='Solve Instantly Up To', default=2000,
                                           config_parameter='operations_research.sync_max_variables')
    or_stuck_minutes = fields.Integer(string='Mark Runs Failed After (min)', default=60,
                                      config_parameter='operations_research.stuck_minutes')
