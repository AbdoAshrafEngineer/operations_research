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


def solve_schedule(tasks, objective='makespan', deadline_mode='soft', time_limit=30,
                   num_workers=1, mip_gap=None):
    """Schedule tasks on disjunctive resources.

    :param tasks: list of dicts with ``key``, ``duration`` (int minutes >= 0),
        ``release`` (int minutes >= 0), ``deadline`` (int minutes or None),
        ``resource`` (hashable or None = unlimited), ``weight`` (float),
        ``predecessors`` (list of keys).
    :param objective: ``makespan`` | ``tardiness`` | ``completion``
    :param deadline_mode: ``hard`` (end <= deadline) or ``soft`` (only penalised by
        the tardiness objective)
    :return: dict with ``status``, ``objective`` (minutes), ``starts``, ``ends``,
        ``lateness`` (minutes late per task), ``wall_time``, ``bound``.
    """
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
            late = model.NewIntVar(0, horizon, 'l_%s' % len(lateness))
            model.AddMaxEquality(late, [0, ends[task['key']] - task['deadline']])
            lateness[task['key']] = late
    weights = {t['key']: t['weight'] for t in tasks}
    # CP-SAT needs integer coefficients: weights are scaled by 100
    if objective == 'tardiness':
        model.Minimize(sum(int(round(weights[k] * 100)) * v for k, v in lateness.items()))
    elif objective == 'completion':
        model.Minimize(sum(int(round(weights[k] * 100)) * ends[k] for k in ends))
    else:
        makespan = model.NewIntVar(0, horizon, 'makespan')
        if ends:
            model.AddMaxEquality(makespan, list(ends.values()))
        model.Minimize(makespan)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit or 30)
    solver.parameters.num_workers = max(int(num_workers or 1), 1)
    if mip_gap:
        solver.parameters.relative_gap_limit = float(mip_gap)
    t0 = time.monotonic()
    status = STATUS.get(solver.Solve(model), 'abnormal')
    result = {
        'status': status,
        'wall_time': time.monotonic() - t0,
        'solver': 'CP-SAT',
        'num_variables': len(model.Proto().variables),
        'num_constraints': len(model.Proto().constraints),
        'objective': 0.0, 'bound': 0.0, 'gap': 0.0,
        'starts': {}, 'ends': {}, 'lateness': {},
    }
    if status in ('optimal', 'feasible'):
        scale = 1.0 if objective == 'makespan' else 100.0
        result['objective'] = solver.ObjectiveValue() / scale
        result['bound'] = solver.BestObjectiveBound() / scale
        if status == 'feasible' and result['objective']:
            result['gap'] = abs(result['objective'] - result['bound']) / abs(result['objective'])
        for key in starts:
            result['starts'][key] = solver.Value(starts[key])
            result['ends'][key] = solver.Value(ends[key])
        for key, var in lateness.items():
            result['lateness'][key] = solver.Value(var)
    return result
