from odoo.tests import BaseCase, tagged

from ..tools import linear


def var(key, lb=0.0, ub=None, vtype='continuous'):
    return {'key': key, 'lb': lb, 'ub': ub, 'type': vtype}


@tagged('post_install', '-at_install', 'operations_research')
class TestLinearTools(BaseCase):
    """Solver layer without ORM: textbook problems with known answers."""

    def _wyndor(self, maximize=True):
        # max 3x + 5y ; x <= 4 ; 2y <= 12 ; 3x + 2y <= 18  -> x=2, y=6, z=36
        return linear.solve_linear(
            [var('x'), var('y')], {'x': 3, 'y': 5},
            [{'key': 'a', 'coefs': {'x': 1}, 'op': '<=', 'rhs': 4},
             {'key': 'b', 'coefs': {'y': 2}, 'op': '<=', 'rhs': 12},
             {'key': 'c', 'coefs': {'x': 3, 'y': 2}, 'op': '<=', 'rhs': 18}],
            maximize=maximize)

    def test_lp_optimal_values(self):
        res = self._wyndor()
        self.assertEqual(res['status'], linear.OPTIMAL)
        self.assertAlmostEqual(res['objective'], 36.0)
        self.assertAlmostEqual(res['values']['x'], 2.0)
        self.assertAlmostEqual(res['values']['y'], 6.0)
        self.assertEqual(res['solver'], 'GLOP')

    def test_lp_sensitivity(self):
        res = self._wyndor()
        self.assertTrue(res['has_sensitivity'])
        self.assertAlmostEqual(res['constraints']['a']['dual'], 0.0)
        self.assertAlmostEqual(res['constraints']['b']['dual'], 1.5)
        self.assertAlmostEqual(res['constraints']['c']['dual'], 1.0)
        self.assertAlmostEqual(res['constraints']['a']['slack'], 2.0)
        self.assertAlmostEqual(res['constraints']['b']['slack'], 0.0)

    def test_minimize_with_ge_constraint(self):
        res = linear.solve_linear([var('x'), var('y')], {'x': 2, 'y': 3},
                                  [{'key': 'd', 'coefs': {'x': 1, 'y': 1}, 'op': '>=', 'rhs': 10}],
                                  maximize=False)
        self.assertAlmostEqual(res['objective'], 20.0)
        self.assertAlmostEqual(res['constraints']['d']['dual'], 2.0)
        self.assertAlmostEqual(res['reduced_costs']['y'], 1.0)

    def test_equality_constraint(self):
        res = linear.solve_linear([var('x'), var('y')], {'x': 1, 'y': 1},
                                  [{'key': 'e', 'coefs': {'x': 1, 'y': 2}, 'op': '=', 'rhs': 8}])
        self.assertEqual(res['status'], linear.OPTIMAL)
        self.assertAlmostEqual(res['values']['x'] + 2 * res['values']['y'], 8.0)
        self.assertAlmostEqual(res['objective'], 8.0)

    def test_integer_rounding(self):
        res = linear.solve_linear([var('x', vtype='integer')], {'x': 1},
                                  [{'key': 'a', 'coefs': {'x': 2}, 'op': '<=', 'rhs': 7}])
        self.assertEqual(res['values']['x'], 3.0)
        self.assertIn(res['solver'], ('SCIP', 'CBC'))
        self.assertFalse(res['has_sensitivity'])

    def test_binary_knapsack(self):
        # weights 5,4,3 ; values 10,40,30 ; capacity 7 -> take 2+3 = 70
        items = {'i1': (5, 10), 'i2': (4, 40), 'i3': (3, 30)}
        res = linear.solve_linear(
            [var(k, vtype='binary') for k in items], {k: v for k, (w, v) in items.items()},
            [{'key': 'cap', 'coefs': {k: w for k, (w, v) in items.items()}, 'op': '<=', 'rhs': 7}])
        self.assertAlmostEqual(res['objective'], 70.0)
        self.assertEqual(res['values'], {'i1': 0.0, 'i2': 1.0, 'i3': 1.0})

    def test_infeasible(self):
        res = linear.solve_linear([var('x')], {'x': 1},
                                  [{'key': 'a', 'coefs': {'x': 1}, 'op': '>=', 'rhs': 5},
                                   {'key': 'b', 'coefs': {'x': 1}, 'op': '<=', 'rhs': 3}])
        self.assertEqual(res['status'], linear.INFEASIBLE)
        self.assertEqual(res['values'], {})

    def test_unbounded_continuous_and_integer(self):
        self.assertEqual(linear.solve_linear([var('x')], {'x': 1}, [])['status'], linear.UNBOUNDED)
        self.assertEqual(linear.solve_linear([var('x', vtype='integer')], {'x': 1}, [])['status'],
                         linear.UNBOUNDED)

    def test_minimize_bounded_by_lower_bounds(self):
        res = linear.solve_linear([var('x', lb=2)], {'x': 1}, [], maximize=False)
        self.assertAlmostEqual(res['objective'], 2.0)

    def test_negative_lower_bound_and_no_lower_bound(self):
        res = linear.solve_linear([var('x', lb=-5, ub=5)], {'x': 1}, [], maximize=False)
        self.assertAlmostEqual(res['values']['x'], -5.0)
        res = linear.solve_linear([var('x', lb=None, ub=3)], {'x': 1}, [], maximize=False)
        self.assertEqual(res['status'], linear.UNBOUNDED)

    def test_empty_objective_feasibility(self):
        res = linear.solve_linear([var('x', ub=10)], {}, [
            {'key': 'a', 'coefs': {'x': 1}, 'op': '>=', 'rhs': 3}])
        self.assertEqual(res['status'], linear.OPTIMAL)
        self.assertGreaterEqual(res['values']['x'], 3.0 - 1e-6)

    def test_empty_constraint_row(self):
        # 0 >= 5 can never hold
        res = linear.solve_linear([var('x', ub=1)], {'x': 1},
                                  [{'key': 'a', 'coefs': {}, 'op': '>=', 'rhs': 5}])
        self.assertEqual(res['status'], linear.INFEASIBLE)

    def test_zero_variables(self):
        res = linear.solve_linear([], {}, [])
        self.assertEqual(res['status'], linear.OPTIMAL)

    def test_status_mapping(self):
        from ortools.linear_solver import pywraplp
        self.assertEqual(linear.map_mp_status(pywraplp.Solver.NOT_SOLVED), linear.NOT_SOLVED)
        self.assertEqual(linear.map_mp_status(pywraplp.Solver.MODEL_INVALID), linear.MODEL_INVALID)
        self.assertEqual(linear.map_mp_status(12345), linear.ABNORMAL)

    def test_mip_gap_parameter_accepted(self):
        res = linear.solve_linear([var('x', vtype='integer', ub=10)], {'x': 1}, [], mip_gap=0.05,
                                  num_threads=2, time_limit=5)
        self.assertEqual(res['values']['x'], 10.0)

    # assignment --------------------------------------------------------
    def _matrix(self):
        costs = {(1, 'a'): 9, (1, 'b'): 2, (1, 'c'): 7,
                 (2, 'a'): 6, (2, 'b'): 4, (2, 'c'): 3,
                 (3, 'a'): 5, (3, 'b'): 8, (3, 'c'): 1}
        return [1, 2, 3], ['a', 'b', 'c'], costs

    def test_assignment_classic(self):
        tasks, resources, costs = self._matrix()
        res = linear.solve_assignment(tasks, resources, costs, {}, {})
        self.assertAlmostEqual(res['objective'], 9.0)
        self.assertEqual(res['assignment'], {1: 'b', 2: 'a', 3: 'c'})

    def test_assignment_maximize(self):
        tasks, resources, costs = self._matrix()
        res = linear.solve_assignment(tasks, resources, costs, {}, {}, maximize=True)
        self.assertAlmostEqual(res['objective'], 7 + 6 + 8)

    def test_assignment_capacity_and_load(self):
        res = linear.solve_assignment([1, 2, 3], ['a', 'b'], {(t, 'a'): 1 for t in (1, 2, 3)},
                                      {1: 2, 2: 2, 3: 1}, {'a': 3, 'b': 2})
        used = {r: sum(load for t, load in {1: 2, 2: 2, 3: 1}.items() if res['assignment'][t] == r)
                for r in ('a', 'b')}
        self.assertLessEqual(used['a'], 3)
        self.assertLessEqual(used['b'], 2)
        self.assertEqual(res['capacity_usage']['a']['activity'], used['a'])

    def test_assignment_allowed_pairs(self):
        tasks, resources, costs = self._matrix()
        allowed = {(1, 'a'), (2, 'b'), (3, 'c')}
        res = linear.solve_assignment(tasks, resources, costs, {}, {}, allowed=allowed)
        self.assertEqual(res['assignment'], {1: 'a', 2: 'b', 3: 'c'})

    def test_assignment_infeasible_when_all_required(self):
        res = linear.solve_assignment([1, 2, 3], ['a'], {}, {}, {'a': 2})
        self.assertEqual(res['status'], linear.INFEASIBLE)
        self.assertEqual(res['assignment'], {1: None, 2: None, 3: None})

    def test_assignment_optional_with_penalty(self):
        res = linear.solve_assignment([1, 2, 3], ['a'], {}, {}, {'a': 2},
                                      assign_all=False, unassigned_penalty=10)
        self.assertEqual(res['status'], linear.OPTIMAL)
        self.assertEqual(sum(1 for r in res['assignment'].values() if r), 2)
        self.assertAlmostEqual(res['objective'], 10.0)

    def test_assignment_task_without_allowed_resource(self):
        res = linear.solve_assignment([1, 2], ['a'], {}, {}, {'a': 5}, allowed={(1, 'a')},
                                      assign_all=False, unassigned_penalty=1)
        self.assertEqual(res['assignment'], {1: 'a', 2: None})
