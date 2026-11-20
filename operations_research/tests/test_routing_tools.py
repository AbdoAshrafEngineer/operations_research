from odoo.tests import BaseCase, tagged

from ..tools.routing import build_matrices, haversine_km, solve_vrp

DEPOT = (30.0444, 31.2357)  # Cairo


def stop(key, dlat, demand=0, **kw):
    return dict({'key': key, 'lat': DEPOT[0] + dlat, 'lng': DEPOT[1], 'demand': demand,
                 'service': 0, 'tw_start': None, 'tw_end': None}, **kw)


def vehicle(key, capacity=0, max_distance_m=0):
    return {'key': key, 'capacity': capacity, 'max_distance_m': max_distance_m}


@tagged('post_install', '-at_install', 'operations_research')
class TestRoutingTools(BaseCase):

    def test_haversine(self):
        self.assertAlmostEqual(haversine_km(30.0444, 31.2357, 31.2001, 29.9187), 180.0, delta=1.0)
        self.assertEqual(haversine_km(10, 10, 10, 10), 0.0)
        self.assertAlmostEqual(haversine_km(0, 0, 0, 180), 20015.1, delta=1.0)

    def test_matrices(self):
        dist, dur = build_matrices([(0, 0), (0, 1)], road_factor=1.0, speed_kmh=111.2)
        self.assertAlmostEqual(dist[0][1], 111195, delta=50)
        self.assertAlmostEqual(dur[0][1], 3600, delta=5)
        self.assertEqual(dist[0][0], 0)

    def test_single_vehicle_visits_all(self):
        stops = [stop(i, 0.01 * i) for i in range(1, 5)]
        res = solve_vrp(DEPOT, stops, [vehicle('v')], time_limit=1)
        self.assertIn(res['status'], ('optimal', 'feasible'))
        self.assertCountEqual(res['routes']['v'], [1, 2, 3, 4])
        self.assertFalse(res['dropped'])
        self.assertGreater(res['objective'], 0)

    def test_capacity_splits_routes(self):
        stops = [stop(i, 0.01 * i, demand=1) for i in range(1, 5)]
        res = solve_vrp(DEPOT, stops, [vehicle('a', 2), vehicle('b', 2)], time_limit=1)
        self.assertEqual(len(res['routes']['a']), 2)
        self.assertEqual(len(res['routes']['b']), 2)

    def test_capacity_infeasible_and_drop(self):
        stops = [stop(i, 0.01 * i, demand=1) for i in range(1, 4)]
        res = solve_vrp(DEPOT, stops, [vehicle('a', 2)], time_limit=1)
        self.assertEqual(res['status'], 'infeasible')
        res = solve_vrp(DEPOT, stops, [vehicle('a', 2)], allow_drop=True, drop_penalty=10 ** 7, time_limit=1)
        self.assertEqual(len(res['dropped']), 1)

    def test_max_distance(self):
        stops = [stop(1, 0.5)]  # ~55 km away
        res = solve_vrp(DEPOT, stops, [vehicle('a', max_distance_m=10000)], time_limit=1)
        self.assertEqual(res['status'], 'infeasible')

    def test_time_windows_and_service(self):
        stops = [stop(1, 0.01, service=600), stop(2, 0.02, tw_start=3600, tw_end=7200)]
        res = solve_vrp(DEPOT, stops, [vehicle('a')], time_limit=1, use_time=True)
        self.assertGreaterEqual(res['eta'][2], 3600)
        self.assertLessEqual(res['eta'][2], 7200)

    def test_impossible_window(self):
        stops = [stop(1, 0.5, tw_end=60)]
        res = solve_vrp(DEPOT, stops, [vehicle('a')], time_limit=1)
        self.assertEqual(res['status'], 'infeasible')

    def test_balance(self):
        # stops north and south of the depot: balancing must use both vehicles
        stops = [stop(i, 0.01 * i) for i in range(1, 4)] + [stop(-i, -0.01 * i) for i in range(1, 4)]
        res = solve_vrp(DEPOT, stops, [vehicle('a'), vehicle('b')], balance=True, time_limit=1)
        self.assertTrue(res['routes']['a'] and res['routes']['b'])
        self.assertAlmostEqual(res['vehicle_km']['a'], res['vehicle_km']['b'], delta=0.5)

    def test_many_stops_uses_metaheuristic(self):
        stops = [stop(i, 0.003 * i) for i in range(1, 15)]
        res = solve_vrp(DEPOT, stops, [vehicle('a')], time_limit=1)
        self.assertEqual(len(res['routes']['a']), 14)
