from datetime import datetime as D

from odoo.tests import BaseCase, tagged

from ..tools.workcal import WorkAxis

DAY1 = [(D(2026, 1, 5, 8), D(2026, 1, 5, 12)), (D(2026, 1, 5, 13), D(2026, 1, 5, 17)),
        (D(2026, 1, 6, 8), D(2026, 1, 6, 12))]


@tagged('post_install', '-at_install', 'operations_research')
class TestWorkAxis(BaseCase):

    def test_real_to_work(self):
        axis = WorkAxis(DAY1, D(2026, 1, 5, 7))
        self.assertEqual(axis.total, 720)
        self.assertEqual(axis.to_work(D(2026, 1, 5, 7)), 0, "before opening")
        self.assertEqual(axis.to_work(D(2026, 1, 5, 12, 30)), 240, "during lunch")
        self.assertEqual(axis.to_work(D(2026, 1, 6, 9)), 540)
        self.assertEqual(axis.to_work(D(2027, 1, 1)), 720, "after the horizon")

    def test_starts_skip_closing_times_and_ends_keep_them(self):
        axis = WorkAxis(DAY1, D(2026, 1, 5, 7))
        self.assertEqual(axis.to_real_start(240), D(2026, 1, 5, 13))
        self.assertEqual(axis.to_real_end(240), D(2026, 1, 5, 12))
        self.assertEqual(axis.to_real_start(480), D(2026, 1, 6, 8))
        self.assertEqual(axis.to_real_end(480), D(2026, 1, 5, 17))
        self.assertEqual(axis.to_real_end(0), D(2026, 1, 5, 8))

    def test_origin_inside_working_time_and_odd_seconds(self):
        axis = WorkAxis(DAY1, D(2026, 1, 5, 10, 0, 30))
        self.assertEqual(axis.intervals[0][0], D(2026, 1, 5, 10, 1), "partial minutes are dropped")
        self.assertEqual(axis.total, 720 - 121)

    def test_overlapping_and_unsorted_intervals_are_merged(self):
        axis = WorkAxis([(D(2026, 1, 5, 10), D(2026, 1, 5, 14)), (D(2026, 1, 5, 8), D(2026, 1, 5, 11))],
                        D(2026, 1, 5))
        self.assertEqual(axis.intervals, [(D(2026, 1, 5, 8), D(2026, 1, 5, 14))])

    def test_beyond_horizon_and_negative(self):
        axis = WorkAxis(DAY1, D(2026, 1, 5))
        with self.assertRaises(ValueError):
            axis.to_real_start(720)
        with self.assertRaises(ValueError):
            axis.to_real_start(-1)
        self.assertEqual(axis.to_real_end(720), D(2026, 1, 6, 12))

    def test_empty_calendar(self):
        axis = WorkAxis([], D(2026, 1, 5))
        self.assertEqual((axis.total, axis.to_work(D(2026, 2, 1))), (0, 0))
        with self.assertRaises(ValueError):
            axis.to_real_start(0)
