from datetime import timedelta

from odoo import api, fields, models


class OrRun(models.Model):
    _inherit = 'or.run'

    @api.model
    def get_or_dashboard_data(self, days=30):
        """KPIs for the Operations Research dashboard (respects record rules)."""
        days = max(int(days or 30), 1)
        since = fields.Datetime.now() - timedelta(days=days)
        Run = self.env['or.run']
        Problem = self.env['or.problem']
        period = [('create_date', '>=', since)]
        finished = Run.search(period + [('state', 'in', ('done', 'applied'))])
        with_solution = finished.filtered('has_solution')
        status_selection = dict(self._fields['solver_status']._description_selection(self.env))
        status_counts = Run._read_group(period + [('solver_status', '!=', False)],
                                        ['solver_status'], ['__count'])
        top = Run._read_group(period, ['problem_id'], ['__count', 'objective_value:avg'],
                              order='__count desc', limit=5)
        recent = Run.search([], limit=8, order='id desc')
        return {
            'days': days,
            'kpis': {
                'problems': Problem.search_count([('parent_id', '=', False)]),
                'scenarios': Problem.search_count([('parent_id', '!=', False)]),
                'scheduled': Problem.search_count([('schedule_active', '=', True)]),
                'runs': Run.search_count(period),
                'to_apply': Run.search_count([('state', '=', 'done'), ('has_solution', '=', True)]),
                'in_progress': Run.search_count([('state', 'in', ('queued', 'running'))]),
                'failed': Run.search_count(period + ['|', ('state', '=', 'failed'),
                                                     ('solver_status', 'in', ('infeasible', 'unbounded',
                                                                              'not_solved', 'abnormal'))]),
                'applied': Run.search_count(period + [('state', '=', 'applied')]),
                'success_rate': round(100.0 * len(with_solution) / len(finished), 1) if finished else 0.0,
                'avg_time': round(sum(finished.mapped('solve_time')) / len(finished), 2) if finished else 0.0,
            },
            'status': [{'key': status, 'label': status_selection.get(status, status), 'count': count}
                       for status, count in status_counts],
            'top_problems': [{'id': problem.id, 'name': problem.display_name, 'count': count,
                              'avg_objective': round(avg or 0.0, 2)} for problem, count, avg in top],
            'recent': [{
                'id': run.id, 'name': run.name, 'problem': run.problem_id.display_name,
                'state': run.state, 'solver_status': run.solver_status or '',
                'status_label': status_selection.get(run.solver_status, ''),
                'objective': round(run.objective_value, 2),
                'date': fields.Datetime.to_string(run.date_done or run.create_date),
            } for run in recent],
        }
