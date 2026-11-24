from odoo.tests import BaseCase, tagged

from ..tools.scheduler import find_cycle, solve_schedule


def task(key, duration, resource=None, preds=(), release=0, deadline=None, weight=1.0):
    return {'key': key, 'duration': duration, 'resource': resource, 'predecessors': list(preds),
            'release': release, 'deadline': deadline, 'weight': weight}


@tagged('post_install', '-at_install', 'operations_research')
class TestSchedulerTools(BaseCase):

    def test_single_machine_makespan(self):
        res = solve_schedule([task(1, 60, 'm'), task(2, 30, 'm'), task(3, 45, 'm')])
        self.assertEqual(res['status'], 'optimal')
        self.assertEqual(res['objective'], 135)
        intervals = sorted((res['starts'][k], res['ends'][k]) for k in (1, 2, 3))
        for (s1, e1), (s2, _e2) in zip(intervals, intervals[1:]):
            self.assertLessEqual(e1, s2)

    def test_parallel_machines(self):
        res = solve_schedule([task(1, 60, 'a'), task(2, 60, 'b')])
        self.assertEqual(res['objective'], 60)

    def test_no_resource_is_unlimited(self):
        res = solve_schedule([task(1, 60), task(2, 60), task(3, 60)])
        self.assertEqual(res['objective'], 60)

    def test_precedence(self):
        res = solve_schedule([task(1, 30, 'a'), task(2, 30, 'b', preds=[1]), task(3, 30, 'c', preds=[2])])
        self.assertEqual(res['starts'][2], res['ends'][1])
        self.assertEqual(res['objective'], 90)

    def test_unknown_predecessor_ignored(self):
        res = solve_schedule([task(1, 30, preds=[99])])
        self.assertEqual(res['status'], 'optimal')

    def test_release(self):
        res = solve_schedule([task(1, 30, release=100)])
        self.assertEqual(res['starts'][1], 100)

    def test_hard_deadline_infeasible(self):
        res = solve_schedule([task(1, 60, 'm', deadline=60), task(2, 60, 'm', deadline=60)],
                             deadline_mode='hard')
        self.assertEqual(res['status'], 'infeasible')

    def test_soft_deadline_tardiness_with_weights(self):
        res = solve_schedule([task(1, 60, 'm', deadline=60, weight=1),
                              task(2, 60, 'm', deadline=60, weight=5)], objective='tardiness')
        self.assertEqual(res['starts'][2], 0, "heavier task goes first")
        self.assertEqual(res['lateness'], {1: 60, 2: 0})
        self.assertAlmostEqual(res['objective'], 60.0)

    def test_weighted_completion(self):
        res = solve_schedule([task(1, 100, 'm', weight=1), task(2, 10, 'm', weight=1)],
                             objective='completion')
        self.assertEqual(res['starts'][2], 0, "shortest job first")
        self.assertAlmostEqual(res['objective'], 10 + 110)

    def test_zero_duration(self):
        res = solve_schedule([task(1, 0, 'm'), task(2, 0, 'm')])
        self.assertEqual(res['status'], 'optimal')
        self.assertEqual(res['objective'], 0)

    def test_empty(self):
        self.assertEqual(solve_schedule([])['status'], 'optimal')

    def test_find_cycle(self):
        self.assertIsNone(find_cycle({1: [], 2: [1], 3: [1, 2]}))
        self.assertEqual(find_cycle({1: [2], 2: [3], 3: [1]})[0], find_cycle({1: [2], 2: [3], 3: [1]})[-1])
        self.assertEqual(find_cycle({1: [1]}), [1, 1])
        self.assertIsNone(find_cycle({1: [42]}))


@tagged('post_install', '-at_install', 'operations_research')
class TestSchedulerTieBreak(BaseCase):

    def test_jobs_off_the_critical_path_start_early(self):
        res = solve_schedule([task(1, 60, 'm'), task(2, 60, 'm'), task(3, 10), task(4, 5, 'x')], time_limit=5)
        self.assertEqual(res['objective'], 120)
        self.assertEqual((res['starts'][3], res['starts'][4]), (0, 0))

    def test_tardiness_keeps_on_time_jobs_early(self):
        res = solve_schedule([task(1, 30, 'm', deadline=30), task(2, 10, deadline=500)],
                             objective='tardiness', time_limit=5)
        self.assertEqual(res['objective'], 0)
        self.assertEqual(res['starts'][2], 0)

    def test_tiny_weights_are_not_lost(self):
        res = solve_schedule([task(1, 60, 'm', deadline=60, weight=0.001),
                              task(2, 60, 'm', deadline=60, weight=0.002)],
                             objective='tardiness', time_limit=5)
        self.assertEqual(res['ends'][2], 60, "the heavier job goes first even with tiny weights")

    def test_negative_input_rejected(self):
        with self.assertRaises(ValueError):
            solve_schedule([task(1, -5)])
        with self.assertRaises(ValueError):
            solve_schedule([task(1, 5, weight=-1)])

    def test_empty_and_zero_duration(self):
        self.assertEqual(solve_schedule([])['status'], 'optimal')
        res = solve_schedule([task(1, 0, 'm'), task(2, 30, 'm')], time_limit=5)
        self.assertEqual(res['objective'], 30)
