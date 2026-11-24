"""Pure-Python CP-SAT job scheduling (no ORM)."""
import time

from ortools.sat.python import cp_model

STATUS = {
    cp_model.OPTIMAL: 'optimal',
    cp_model.FEASIBLE: 'feasible',
    cp_model.INFEASIBLE: 'infeasible',
    cp_model.MODEL_INVALID: 'model_invalid',
    cp_model.UNKNOWN: 'not_solved',
}


def find_cycle(predecessors):
    """Return a list of keys forming a precedence cycle, or None.

    :param predecessors: dict ``task -> iterable of predecessor tasks``
    """
    WHITE, GREY, BLACK = 0, 1, 2
    color = dict.fromkeys(predecessors, WHITE)
    stack = []

    def visit(node):
        color[node] = GREY
        stack.append(node)
        for pred in predecessors.get(node, ()):
            if pred not in color:
                continue
            if color[pred] == GREY:
                return stack[stack.index(pred):] + [pred]
            if color[pred] == WHITE:
                cycle = visit(pred)
                if cycle:
                    return cycle
        stack.pop()
        color[node] = BLACK
        return None

    for node in list(predecessors):
        if color[node] == WHITE:
            cycle = visit(node)
            if cycle:
                return cycle
    return None


WEIGHT_RESOLUTION = 1000  # the largest weight becomes 1000: ratios kept to 0.1%


def _weight_factor(tasks):
    """CP-SAT needs integer coefficients: scale weights so the largest one is 1000.

    Relative scaling keeps tiny weights (0.001 vs 0.002) apart and huge ones from overflowing.
    """
    biggest = max((t['weight'] for t in tasks), default=0)
    return WEIGHT_RESOLUTION / biggest if biggest > 0 else 1.0


def _scaled_weight(weight, factor):
    """Integer weight; any positive weight stays positive."""
    if weight <= 0:
        return 0
    return max(1, int(round(weight * factor)))


def solve_schedule(tasks, objective='makespan', deadline_mode='soft', time_limit=30,
                   num_workers=1, mip_gap=None, tie_break=True):
    """Schedule tasks on disjunctive resources.

    :param tasks: list of dicts with ``key``, ``duration`` (int minutes >= 0),
        ``release`` (int minutes >= 0), ``deadline`` (int minutes or None),
        ``resource`` (hashable or None = unlimited), ``weight`` (float >= 0),
        ``predecessors`` (list of keys).
    :param objective: ``makespan`` | ``tardiness`` | ``completion``
    :param deadline_mode: ``hard`` (end <= deadline) or ``soft`` (only penalised by
        the tardiness objective)
    :param tie_break: after the main goal, use the remaining time to start every task as
        early as possible without worsening the main goal (otherwise tasks that do not
        affect the makespan/lateness may be placed anywhere).
    :return: dict with ``status``, ``objective`` (minutes), ``starts``, ``ends``,
        ``lateness`` (minutes late per task), ``wall_time``, ``bound``.
    """
    for task in tasks:
        if task['duration'] < 0:
            raise ValueError("negative duration for task %r" % (task['key'],))
        if task['weight'] < 0:
            raise ValueError("negative weight for task %r" % (task['key'],))
    model = cp_model.CpModel()
    durations = sum(t['duration'] for t in tasks)
    max_release = max((t['release'] for t in tasks), default=0)
    max_deadline = max((t['deadline'] for t in tasks if t['deadline'] is not None), default=0)
    horizon = max(max_release + durations, max_deadline) + 1
    starts, ends, intervals = {}, {}, {}
    for task in tasks:
        key = task['key']
        start = model.NewIntVar(task['release'], horizon, 's_%s' % len(starts))
        end = model.NewIntVar(0, horizon, 'e_%s' % len(ends))
        interval = model.NewIntervalVar(start, task['duration'], end, 'i_%s' % len(intervals))
        starts[key], ends[key], intervals[key] = start, end, interval
        if deadline_mode == 'hard' and task['deadline'] is not None:
            model.Add(end <= task['deadline'])
    by_resource = {}
    for task in tasks:
        if task['resource'] is not None:
            by_resource.setdefault(task['resource'], []).append(intervals[task['key']])
    for group in by_resource.values():
        if len(group) > 1:
            model.AddNoOverlap(group)
    for task in tasks:
        for pred in task['predecessors']:
            if pred in ends:
                model.Add(starts[task['key']] >= ends[pred])
    lateness = {}
    for task in tasks:
        if task['deadline'] is not None:
            late = model.NewIntVar(0, horizon + max(0, -task['deadline']), 'l_%s' % len(lateness))
            model.AddMaxEquality(late, [0, ends[task['key']] - task['deadline']])
            lateness[task['key']] = late
    factor = _weight_factor(tasks)
    weights = {t['key']: _scaled_weight(t['weight'], factor) for t in tasks}
    if objective == 'tardiness':
        primary = sum(weights[k] * v for k, v in lateness.items())
        scale = factor
    elif objective == 'completion':
        primary = sum(weights[k] * ends[k] for k in ends)
        scale = factor
    else:
        makespan = model.NewIntVar(0, horizon, 'makespan')
        if ends:
            model.AddMaxEquality(makespan, list(ends.values()))
        primary = makespan
        scale = 1.0
    model.Minimize(primary)

    total_time = float(time_limit or 30)
    second_phase = tie_break and objective != 'completion' and len(tasks) > 1
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = total_time * (0.75 if second_phase else 1.0)
    solver.parameters.num_workers = max(int(num_workers or 1), 1)
    solver.parameters.random_seed = 0
    if mip_gap:
        solver.parameters.relative_gap_limit = float(mip_gap)
    t0 = time.monotonic()
    status = STATUS.get(solver.Solve(model), 'abnormal')
    result = {
        'status': status,
        'wall_time': 0.0,
        'solver': 'CP-SAT',
        'num_variables': len(model.Proto().variables),
        'num_constraints': len(model.Proto().constraints),
        'objective': 0.0, 'bound': 0.0, 'gap': 0.0,
        'starts': {}, 'ends': {}, 'lateness': {},
    }
    if status not in ('optimal', 'feasible'):
        result['wall_time'] = time.monotonic() - t0
        return result
    best = int(round(solver.ObjectiveValue()))
    result['objective'] = best / scale
    result['bound'] = solver.BestObjectiveBound() / scale
    if status == 'feasible' and result['objective']:
        result['gap'] = abs(result['objective'] - result['bound']) / abs(result['objective'])
    values = {key: (solver.Value(starts[key]), solver.Value(ends[key])) for key in starts}

    if second_phase:
        # keep the main goal at its best value, then pull every task as early as possible
        model.Add(primary <= best)
        for key, (start_value, _end) in values.items():
            model.AddHint(starts[key], start_value)
        model.Minimize(sum(ends.values()))
        # the tidy-up pass gets at most a quarter of the time limit: it rarely proves optimality
        remaining = max(total_time - (time.monotonic() - t0), 1.0)
        solver.parameters.max_time_in_seconds = min(remaining, max(1.0, total_time * 0.25))
        solver.parameters.relative_gap_limit = 0.0
        if STATUS.get(solver.Solve(model)) in ('optimal', 'feasible'):
            values = {key: (solver.Value(starts[key]), solver.Value(ends[key])) for key in starts}

    for key, (start_value, end_value) in values.items():
        result['starts'][key] = start_value
        result['ends'][key] = end_value
    by_key = {t['key']: t for t in tasks}
    for key in lateness:
        result['lateness'][key] = max(0, values[key][1] - by_key[key]['deadline'])
    result['wall_time'] = time.monotonic() - t0
    return result
