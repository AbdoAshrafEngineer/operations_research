"""Pure-Python vehicle routing with OR-Tools (no ORM)."""
import math
import time

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

EARTH_RADIUS_KM = 6371.0088
BIG = 10 ** 9


def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in km."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def build_matrices(points, road_factor=1.3, speed_kmh=40.0):
    """Distance (metres) and travel time (seconds) integer matrices."""
    n = len(points)
    dist = [[0] * n for _ in range(n)]
    dur = [[0] * n for _ in range(n)]
    speed = max(speed_kmh, 1e-6)
    for i in range(n):
        for j in range(n):
            if i != j:
                km = haversine_km(*points[i], *points[j]) * road_factor
                dist[i][j] = int(round(km * 1000))
                dur[i][j] = int(round(km / speed * 3600))
    return dist, dur


def solve_vrp(depot, stops, vehicles, road_factor=1.3, speed_kmh=40.0, allow_drop=False,
              drop_penalty=0, balance=False, time_limit=30, use_time=False):
    """Capacitated VRP with optional time windows and distance limits.

    :param depot: ``(lat, lng)``
    :param stops: list of dicts ``key, lat, lng, demand, service (s), tw_start (s|None), tw_end (s|None)``
    :param vehicles: list of dicts ``key, capacity (0 = unlimited), max_distance_m (0 = unlimited)``
    :return: dict with ``status``, ``objective`` (km), ``routes`` {vehicle_key: [stop keys]},
        ``eta`` {stop key: seconds}, ``dropped`` [keys], ``vehicle_km`` {vehicle_key: km}.
    """
    t0 = time.monotonic()
    points = [depot] + [(s['lat'], s['lng']) for s in stops]
    dist, dur = build_matrices(points, road_factor, speed_kmh)
    service = [0] + [int(s.get('service') or 0) for s in stops]
    manager = pywrapcp.RoutingIndexManager(len(points), len(vehicles), 0)
    routing = pywrapcp.RoutingModel(manager)

    def dist_cb(i, j):
        return dist[manager.IndexToNode(i)][manager.IndexToNode(j)]
    dist_idx = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(dist_idx)
    limits = [v['max_distance_m'] or BIG for v in vehicles]
    routing.AddDimensionWithVehicleCapacity(dist_idx, 0, limits, True, 'Distance')
    if balance:
        routing.GetDimensionOrDie('Distance').SetGlobalSpanCostCoefficient(100)

    demands = [0] + [int(round(s.get('demand') or 0)) for s in stops]
    if any(demands):
        def demand_cb(i):
            return demands[manager.IndexToNode(i)]
        dem_idx = routing.RegisterUnaryTransitCallback(demand_cb)
        caps = [int(round(v['capacity'])) if v['capacity'] else BIG for v in vehicles]
        routing.AddDimensionWithVehicleCapacity(dem_idx, 0, caps, True, 'Capacity')

    windows = any(s.get('tw_start') is not None or s.get('tw_end') is not None for s in stops)
    if use_time or windows or any(service):
        horizon = max([s['tw_end'] for s in stops if s.get('tw_end') is not None] + [0])
        horizon = max(horizon, sum(service) + sum(max(row) for row in dur)) + 1

        def time_cb(i, j):
            a, b = manager.IndexToNode(i), manager.IndexToNode(j)
            return dur[a][b] + service[a]
        time_idx = routing.RegisterTransitCallback(time_cb)
        routing.AddDimension(time_idx, horizon, horizon, False, 'Time')
        time_dim = routing.GetDimensionOrDie('Time')
        for node, stop in enumerate(stops, 1):
            index = manager.NodeToIndex(node)
            start = stop.get('tw_start') or 0
            end = stop['tw_end'] if stop.get('tw_end') is not None else horizon
            time_dim.CumulVar(index).SetRange(max(0, start), max(0, end))
        for v in range(len(vehicles)):
            time_dim.CumulVar(routing.Start(v)).SetRange(0, 0)
    else:
        time_dim = None

    if allow_drop:
        for node in range(1, len(points)):
            routing.AddDisjunction([manager.NodeToIndex(node)], int(drop_penalty))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    if len(stops) > 10:
        params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    params.time_limit.FromSeconds(max(int(time_limit or 30), 1))
    solution = routing.SolveWithParameters(params)
    search_status = routing.status()

    result = {
        'solver': 'OR-Tools Routing',
        'num_variables': routing.Size() + routing.vehicles(),
        'num_constraints': 0,
        'objective': 0.0, 'bound': 0.0, 'gap': 0.0,
        'routes': {}, 'eta': {}, 'dropped': [], 'vehicle_km': {}, 'leg_km': {},
        'wall_time': 0.0,
    }
    statuses = routing_enums_pb2.RoutingSearchStatus
    if solution is None:
        if search_status == statuses.ROUTING_FAIL_TIMEOUT:
            result['status'] = 'not_solved'
        elif search_status == statuses.ROUTING_INVALID:
            result['status'] = 'model_invalid'
        else:
            result['status'] = 'infeasible'
        result['wall_time'] = time.monotonic() - t0
        return result

    result['status'] = 'optimal' if search_status == statuses.ROUTING_OPTIMAL else 'feasible'
    total = 0
    for v, vehicle in enumerate(vehicles):
        index = routing.Start(v)
        route, meters = [], 0
        prev_node, clock = 0, 0
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            if node:
                key = stops[node - 1]['key']
                route.append(key)
                result['leg_km'][key] = dist[prev_node][node] / 1000.0
                clock += service[prev_node] + dur[prev_node][node]
                if time_dim is not None:
                    clock = solution.Min(time_dim.CumulVar(index))
                result['eta'][key] = clock
                prev_node = node
            next_index = solution.Value(routing.NextVar(index))
            meters += routing.GetArcCostForVehicle(index, next_index, v)
            index = next_index
        result['routes'][vehicle['key']] = route
        result['vehicle_km'][vehicle['key']] = meters / 1000.0
        total += meters
    served = {key for route in result['routes'].values() for key in route}
    result['dropped'] = [s['key'] for s in stops if s['key'] not in served]
    result['objective'] = total / 1000.0
    result['bound'] = result['objective']
    result['wall_time'] = time.monotonic() - t0
    return result
