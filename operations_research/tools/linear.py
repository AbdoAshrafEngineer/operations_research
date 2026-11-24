"""Pure-Python wrappers around OR-Tools linear solvers.

These functions take plain Python data (no ORM) so they can be unit tested
in isolation and reused by any template module.
"""
import logging
import math
import time

_logger = logging.getLogger(__name__)

try:
    from ortools.linear_solver import pywraplp
except ImportError:  # pragma: no cover - checked at install via external_dependencies
    pywraplp = None

EPS = 1e-7


def ortools_version():
    """Installed OR-Tools version, stored on each run for reproducibility."""
    try:
        import ortools
    except ImportError:  # pragma: no cover
        return False
    return getattr(ortools, '__version__', False)

# Solver statuses stored on or.run.solver_status
OPTIMAL = 'optimal'
FEASIBLE = 'feasible'
INFEASIBLE = 'infeasible'
UNBOUNDED = 'unbounded'
ABNORMAL = 'abnormal'
MODEL_INVALID = 'model_invalid'
NOT_SOLVED = 'not_solved'
SOLUTION_STATUSES = (OPTIMAL, FEASIBLE)


def map_mp_status(status):
    """Map a pywraplp result status to our status keys."""
    mapping = {
        pywraplp.Solver.OPTIMAL: OPTIMAL,
        pywraplp.Solver.FEASIBLE: FEASIBLE,
        pywraplp.Solver.INFEASIBLE: INFEASIBLE,
        pywraplp.Solver.UNBOUNDED: UNBOUNDED,
        pywraplp.Solver.ABNORMAL: ABNORMAL,
        pywraplp.Solver.MODEL_INVALID: MODEL_INVALID,
        pywraplp.Solver.NOT_SOLVED: NOT_SOLVED,
    }
    return mapping.get(status, ABNORMAL)


def _create_solver(is_mip):
    names = ('SCIP', 'CBC') if is_mip else ('GLOP', 'PDLP')
    for name in names:
        solver = pywraplp.Solver.CreateSolver(name)
        if solver:
            return solver, name
    raise RuntimeError("No OR-Tools %s solver available" % ('MIP' if is_mip else 'LP'))


def _finite(value):
    return value is not None and not (isinstance(value, float) and math.isinf(value))


def solve_linear(variables, objective, constraints, maximize=True, time_limit=30,
                 num_threads=1, mip_gap=None):
    """Solve a linear / mixed-integer program.

    :param variables: list of dicts ``{'key', 'lb', 'ub', 'type'}`` where type is
        ``continuous``, ``integer`` or ``binary``. ``lb``/``ub`` may be ``None``
        for -inf/+inf.
    :param objective: dict ``{var_key: coefficient}``
    :param constraints: list of dicts ``{'key', 'coefs': {var_key: coef},
        'op': '<=' | '>=' | '=', 'rhs': float}``
    :return: dict with ``status``, ``objective``, ``bound``, ``gap``, ``values``,
        ``reduced_costs``, ``constraints`` (key -> activity/slack/dual),
        ``has_sensitivity``, ``solver``, ``wall_time``.
    """
    if pywraplp is None:
        raise RuntimeError("Google OR-Tools is not installed (pip install ortools)")
    is_mip = any(v['type'] in ('integer', 'binary') for v in variables)
    solver, solver_name = _create_solver(is_mip)
    inf = solver.infinity()

    mp_vars = {}
    for index, var in enumerate(variables):
        if var['type'] == 'binary':
            lb, ub = 0.0, 1.0
        else:
            lb = var.get('lb') if _finite(var.get('lb')) else -inf
            ub = var.get('ub') if _finite(var.get('ub')) else inf
        name = 'x%d' % index
        if var['type'] in ('integer', 'binary'):
            mp_vars[var['key']] = solver.IntVar(lb, ub, name)
        else:
            mp_vars[var['key']] = solver.NumVar(lb, ub, name)

    mp_cons = {}
    for index, cons in enumerate(constraints):
        rhs = float(cons['rhs'])
        if cons['op'] == '<=':
            lb, ub = -inf, rhs
        elif cons['op'] == '>=':
            lb, ub = rhs, inf
        else:
            lb, ub = rhs, rhs
        ct = solver.Constraint(lb, ub, 'c%d' % index)
        for var_key, coef in cons['coefs'].items():
            if coef:
                ct.SetCoefficient(mp_vars[var_key], float(coef))
        mp_cons[cons['key']] = ct

    obj = solver.Objective()
    for var_key, coef in objective.items():
        if coef:
            obj.SetCoefficient(mp_vars[var_key], float(coef))
    if maximize:
        obj.SetMaximization()
    else:
        obj.SetMinimization()

    if time_limit:
        solver.SetTimeLimit(int(max(time_limit, 0.1) * 1000))
    if num_threads and num_threads > 1:
        solver.SetNumThreads(int(num_threads))  # ignored by solvers without threading
    params = pywraplp.MPSolverParameters()
    if is_mip and mip_gap:
        params.SetDoubleParam(pywraplp.MPSolverParameters.RELATIVE_MIP_GAP, float(mip_gap))

    start = time.monotonic()
    status = map_mp_status(solver.Solve(params))
    wall = time.monotonic() - start

    result = {
        'status': status,
        'solver': solver_name,
        'wall_time': wall,
        'num_variables': solver.NumVariables(),
        'num_constraints': solver.NumConstraints(),
        'objective': 0.0,
        'bound': 0.0,
        'gap': 0.0,
        'values': {},
        'reduced_costs': {},
        'constraints': {},
        'has_sensitivity': False,
    }
    if status == INFEASIBLE and any(objective.values()):
        # GLOP/SCIP may report a dual-infeasible (unbounded) model as INFEASIBLE.
        # Re-solve as a pure feasibility problem to tell the two apart.
        feasibility = solve_linear(variables, {}, constraints, maximize=maximize,
                                   time_limit=time_limit, num_threads=num_threads)
        if feasibility['status'] in SOLUTION_STATUSES:
            result['status'] = status = UNBOUNDED
    if status not in SOLUTION_STATUSES:
        return result

    result['objective'] = obj.Value()
    if is_mip:
        result['bound'] = obj.BestBound()
        denom = max(abs(result['objective']), EPS)
        result['gap'] = abs(result['bound'] - result['objective']) / denom if status == FEASIBLE else 0.0
    else:
        result['bound'] = result['objective']

    for key, var in mp_vars.items():
        value = var.solution_value()
        if var.integer():
            value = float(round(value))
        result['values'][key] = value

    sensitivity = not is_mip and status == OPTIMAL
    result['has_sensitivity'] = sensitivity
    activities = solver.ComputeConstraintActivities() if mp_cons else []
    for (key, ct), activity in zip(mp_cons.items(), activities):
        lb, ub = ct.lb(), ct.ub()
        if ub < inf and lb > -inf:
            slack = abs(ub - activity) if lb == ub else min(ub - activity, activity - lb)
        elif ub < inf:
            slack = ub - activity
        else:
            slack = activity - lb
        if abs(slack) < 1e-9:
            slack = 0.0
        result['constraints'][key] = {
            'activity': activity,
            'slack': slack,
            'dual': ct.dual_value() if sensitivity else 0.0,
        }
    if sensitivity:
        for key, var in mp_vars.items():
            result['reduced_costs'][key] = var.reduced_cost()
    return result


def solve_assignment(tasks, resources, costs, loads, capacities, allowed=None,
                     assign_all=True, unassigned_penalty=0.0, maximize=False,
                     time_limit=30, num_threads=1):
    """Generalised assignment problem (each task to at most/exactly one resource).

    :param tasks: list of task keys
    :param resources: list of resource keys
    :param costs: dict ``(task, resource) -> cost`` (or value when maximizing)
    :param loads: dict ``task -> load`` consumed on the resource
    :param capacities: dict ``resource -> capacity``
    :param allowed: optional set of allowed ``(task, resource)`` pairs
    :return: dict like :func:`solve_linear` plus ``assignment`` ``{task: resource|None}``
    """
    variables, objective = [], {}
    pairs = []
    for task in tasks:
        for res in resources:
            if allowed is not None and (task, res) not in allowed:
                continue
            key = (task, res)
            pairs.append(key)
            variables.append({'key': key, 'type': 'binary'})
            objective[key] = costs.get(key, 0.0)
    constraints = []
    for task in tasks:
        task_pairs = [p for p in pairs if p[0] == task]
        if assign_all:
            constraints.append({'key': ('task', task), 'coefs': dict.fromkeys(task_pairs, 1.0),
                                'op': '=', 'rhs': 1.0})
        else:
            # slack variable u_t = 1 when task stays unassigned
            u_key = ('unassigned', task)
            variables.append({'key': u_key, 'type': 'binary'})
            if not maximize:
                objective[u_key] = unassigned_penalty
            coefs = dict.fromkeys(task_pairs, 1.0)
            coefs[u_key] = 1.0
            constraints.append({'key': ('task', task), 'coefs': coefs, 'op': '=', 'rhs': 1.0})
    for res in resources:
        res_pairs = [p for p in pairs if p[1] == res]
        if not res_pairs:
            continue
        constraints.append({'key': ('res', res),
                            'coefs': {p: loads.get(p[0], 1.0) for p in res_pairs},
                            'op': '<=', 'rhs': capacities.get(res, 1.0)})
    result = solve_linear(variables, objective, constraints, maximize=maximize,
                          time_limit=time_limit, num_threads=num_threads)
    assignment = dict.fromkeys(tasks)
    if result['status'] in SOLUTION_STATUSES:
        for key in pairs:
            if result['values'].get(key, 0.0) > 0.5:
                assignment[key[0]] = key[1]
    result['assignment'] = assignment
    result['capacity_usage'] = {
        key[1]: info for key, info in result['constraints'].items() if key[0] == 'res'
    }
    return result
