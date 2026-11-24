from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import OrCase


@tagged('post_install', '-at_install', 'operations_research')
class TestRunSecurity(OrCase):
    """A run decides whose rights are used to read and write business data:
    nobody may change that except the module itself."""

    def _user_run(self):
        items, problem = self.wyndor()
        problem.time_limit = 5
        run = problem.with_user(self.user)._or_new_run()
        return items, problem, run

    def test_user_cannot_tamper_with_run(self):
        items, problem, run = self._user_run()
        run = run.with_user(self.user)
        for vals in ({'user_id': self.env.ref('base.user_admin').id},
                     {'auto_apply': True},
                     {'state': 'done', 'solver_status': 'optimal'},
                     {'config_signature': 'x'},
                     {'problem_id': problem.id}):
            with self.assertRaises(AccessError, msg=vals):
                run.write(vals)
        self.assertEqual((run.user_id, run.auto_apply, run.state), (self.user, False, 'draft'))

    def test_manager_cannot_edit_run_either(self):
        _items, _problem, run = self._user_run()
        with self.assertRaises(AccessError):
            run.with_user(self.manager).write({'user_id': self.manager.id})

    def test_runs_only_created_by_solving(self):
        _items, problem = self.wyndor()
        for user in (self.user, self.designer, self.manager):
            with self.assertRaises(AccessError):
                self.env['or.run'].with_user(user).create({'problem_id': problem.id, 'user_id': self.env.uid})
        run = self.solve(problem, self.user)
        self.assertEqual((run.user_id, run.state), (self.user, 'done'))

    def test_only_owner_or_manager_restarts_run(self):
        _items, problem, run = self._user_run()
        other = self.designer
        for method in ('action_queue', 'action_solve_now', 'action_cancel'):
            with self.assertRaises(AccessError, msg=method):
                getattr(run.with_user(other), method)()
        run.with_user(self.user).action_solve_now()
        self.assertEqual(run.state, 'done')
        run.with_user(self.manager).action_cancel()
        self.assertEqual(run.state, 'cancelled')

    def test_user_cannot_apply_or_run_as_someone_else(self):
        items, problem, run = self._user_run()
        run.with_user(self.user).action_solve_now()
        with self.assertRaises(AccessError):
            run.with_user(self.user).action_apply()
        with self.assertRaises(AccessError):
            problem.with_user(self.designer)._or_new_run(user=self.manager)
        self.assertEqual(items.mapped('x_qty'), [0.0, 0.0])

    def test_users_can_comment_on_runs(self):
        _items, _problem, run = self._user_run()
        run.with_user(self.user).message_post(body="Looks good")
        self.assertIn("Looks good", run.message_ids[0].body)

    def test_schedule_run_as_restricted_to_managers(self):
        _items, problem = self.wyndor()
        designer_problem = problem.with_user(self.designer)
        with self.assertRaises(AccessError):
            designer_problem.write({'schedule_user_id': self.manager.id})
        designer_problem.write({'schedule_user_id': self.designer.id})
        with self.assertRaises(AccessError):
            self.env['or.problem'].with_user(self.designer).create({
                'name': 'x', 'problem_type': 'lp', 'model_id': self.item_model.id,
                'schedule_user_id': self.manager.id})
        problem.with_user(self.manager).write({'schedule_user_id': self.designer.id})
        self.assertEqual(problem.schedule_user_id, self.designer)

    def test_copy_does_not_keep_run_as(self):
        _items, problem = self.wyndor()
        problem.schedule_user_id = self.manager
        copy = problem.with_user(self.designer).copy()
        self.assertEqual(copy.schedule_user_id, self.designer)

    def test_non_manager_edit_turns_off_auto_apply(self):
        _items, problem = self.wyndor()
        problem.write({'schedule_user_id': self.manager.id, 'schedule_auto_apply': True})
        problem.with_user(self.manager).write({'time_limit': 7})
        self.assertTrue(problem.schedule_auto_apply, "a manager edit keeps the approval")
        problem.with_user(self.designer).write({'time_limit': 8})
        self.assertFalse(problem.schedule_auto_apply)
        self.assertIn('Automatic apply was turned off', problem.message_ids[0].body)

    def test_scheduled_auto_apply_still_works(self):
        items, problem = self.wyndor()
        problem.write({'schedule_active': True, 'schedule_user_id': self.manager.id,
                       'schedule_auto_apply': True, 'schedule_next_date': '2020-01-01 00:00:00'})
        runs = self.env['or.problem']._cron_run_scheduled()
        self.env['or.run']._cron_process_queue()
        self.assertEqual(runs.state, 'applied')
        self.assertEqual(items.mapped('x_qty'), [2.0, 6.0])
