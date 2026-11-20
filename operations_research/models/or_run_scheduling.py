from odoo import api, fields, models


class OrRun(models.Model):
    _inherit = 'or.run'

    def action_open_gantt(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'operations_research.gantt',
            'name': self.env._("Schedule %s", self.name),
            'params': {'run_id': self.id},
        }

    def get_gantt_data(self):
        """Data for the Owl Gantt chart (rows per resource)."""
        self.ensure_one()
        self.check_access('read')
        lines = self.line_ids.filtered('sched_start')
        rows = {}
        for line in lines:
            key = line.assigned_res_id or 0
            row = rows.setdefault(key, {
                'id': key,
                'name': line.assigned_name or self.env._("No resource"),
                'tasks': [],
            })
            row['tasks'].append({
                'id': line.id,
                'res_id': line.res_id,
                'res_model': line.res_model,
                'name': line.res_name,
                'start': fields.Datetime.to_string(line.sched_start),
                'end': fields.Datetime.to_string(line.sched_end),
                'late_minutes': line.sched_late_minutes,
                'changed': line.changed,
            })
        starts = lines.mapped('sched_start')
        ends = lines.mapped('sched_end')
        return {
            'run': {'id': self.id, 'name': self.name, 'problem': self.problem_id.name,
                    'status': self.solver_status, 'objective': self.objective_value},
            'start': fields.Datetime.to_string(min(starts)) if starts else False,
            'end': fields.Datetime.to_string(max(ends)) if ends else False,
            'rows': sorted(rows.values(), key=lambda r: (r['id'] == 0, r['name'])),
        }


class OrRunLine(models.Model):
    _inherit = 'or.run.line'

    sched_start = fields.Datetime(string='Planned Start')
    sched_end = fields.Datetime(string='Planned End')
    sched_start_before = fields.Datetime(string='Current Start')
    sched_late_minutes = fields.Integer(string='Late (min)')

    @api.depends('sched_start', 'sched_start_before')
    def _compute_delta(self):
        super()._compute_delta()
        for line in self.filtered('sched_start'):
            line.changed = line.changed or line.sched_start != line.sched_start_before
