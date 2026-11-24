===================================
Operations Research for Odoo
===================================

Find the best plan on your own Odoo records with Google OR-Tools, review what would
change, then apply it.

Every plan has three parts:

* **Decide** - the field the solver chooses (a quantity, a person, a date, a vehicle).
* **Goal** - what to maximise or minimise (revenue, cost, kilometres, finish time).
* **Limits** - what cannot be exceeded (budget, space, capacity, one job at a time).

Video: https://www.youtube.com/watch?v=4bLPr5Jh78U

Problem types
=============

* Linear / integer programs (GLOP, SCIP, CBC) with sensitivity analysis and
  "why not?" explanations for infeasible problems.
* Assignment (who does what).
* Scheduling with CP-SAT (resources, predecessors, deadlines) and a Gantt view.
* Vehicle routing (capacity, time windows, shortest or balanced) and a map view.

Installation
============

1. ``pip install "ortools>=9.10,<10"`` on the Odoo server (see ``requirements.txt``).
2. Add the module to the addons path, update the Apps list, install
   **Operations Research**.
3. Give users one of the roles: *User* (solve, review), *Designer* (build problems),
   *Manager* (apply, configure, schedule).
4. Open *Operations Research > Tutorials*: one narrated video and 7 guided tours.

Configuration
=============

*Settings > Operations Research*: default time limit, size limits for interactive
solving, number of solver workers, "stuck run" timeout, routing speed (km/h) and
road factor (straight-line distance multiplier).

Usage
=====

1. *Problems > New*: pick the type and the model, filter the records with a domain.
2. Fill in Decide / Goal / Limits with the field pickers. Values can follow links,
   e.g. ``product_id.standard_price``.
3. *Solve* (or *Solve in Background*). Review current vs proposed values, the Gantt
   or the map.
4. *Apply*. If a record changed since the solve, the run stops with a conflict list;
   *Force Apply* is reserved to managers.

Runs are executed with the access rights of the user who launched them.

Extending (developer plugins)
=============================

A new problem type is a small addon::

    class OrProblem(models.Model):
        _inherit = 'or.problem'

        problem_type = fields.Selection(selection_add=[('my_type', 'My Type')],
                                        ondelete={'my_type': 'cascade'})

        def _or_check_my_type(self): ...
        def _or_solve_my_type(self, run): ...          # fill run.line_ids
        def _or_apply_my_type(self, run, lines): ...   # write the proposal
        def _or_snapshot_my_type(self, records): ...   # values used for conflicts
        def _or_written_fields_my_type(self): ...      # fields Apply writes (kept for Undo)
        def _or_setup_checks_my_type(self, records): ...  # optional: Check Setup messages

The queue, review/apply workflow, scenarios, schedules, security and dashboard
work unchanged.

Tests
=====

``odoo-bin -d <db> -i operations_research --test-tags /operations_research --stop-after-init``

Changelog
=========

1.2.1
-----

* Problem Builder stepper: the current step is white on the brand purple, finished steps are
  green with a check, upcoming steps are grey; every coloured label meets 4.5:1 contrast and
  the layout mirrors correctly in Arabic (right to left). Arabic translation completed.

1.2.0
-----

* Build a Problem: a six-screen guided builder (question, records, decide, goal, limits,
  review). Odoo pre-fills every answer from the model's own field names, shows only the
  fields that fit each answer, and sums up the problem in plain words before creating it.
* Working hours follow the user's time zone on Odoo 20 (calendars there have no time zone).

1.1.0
-----

* Security: runs can no longer be edited directly; only the module changes their state,
  "Run As" and "Auto Apply". Only managers can make a scheduled problem run as another
  user, and a non-manager edit turns automatic apply off.
* Scheduling can plan inside working hours and public holidays (``resource.calendar``);
  jobs not finished today continue the next working day. Tasks off the critical path now
  start as early as possible. Priority weights keep their ratios at any size.
* Routing counts decimal loads (0.4 m3, 2.5 pallets) exactly and explains, before solving,
  stops bigger than any vehicle, total load above the fleet, negative loads and reversed
  delivery windows; when no plan fits it names the stops that cannot fit.
* Long solves always run in the background, and the time limit is kept under the server's
  worker limit so a run is never killed half-way.
* 19 templates built on standard Odoo fields; missing apps, fields or filters are reported,
  never fatal.
* Check Setup: blockers, warnings and tips in plain words before solving.
* Benefit: the same measure for the current plan and the proposal (goal value, cost,
  hours, lateness, kilometres). A current plan that breaks a limit is reported instead of
  compared.
* Apply only selected result lines; Undo Apply puts back the previous values and protects
  later edits (Force Undo to override).
* The OR-Tools version is stored on each run.

License
=======

LGPL-3. Author: Abdelrahman Ashraf - https://edu-abdelrahman-ashraf.odoo.com
