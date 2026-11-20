import { _t } from "@web/core/l10n/translation";
import { guideRegistry } from "./or_guide_service";

/** Highlight a form field together with its label (falls back to the widget). */
const field = (name) => `.o_field_widget[name=${name}]`;
const tab = (name) => `.o_notebook a[name=${name}]`;
const openTab = (name) => async (h) => h.click(tab(name));

const PROBLEM_ACTION = "operations_research.or_problem_action";
const NEW_LP = { default_problem_type: "lp" };

guideRegistry.add("welcome", {
    sequence: 1,
    name: _t("Welcome: find your way"),
    description: _t("A 2-minute walk through the app: dashboard, problems, runs and who can do what."),
    icon: "fa-compass",
    video: "operations_research_for_odoo.mp4",
    steps: () => [
        {
            before: (h) => h.openAction("operations_research.or_dashboard_action"),
            title: _t("Welcome to Operations Research"),
            text: _t("This app finds the best answer to everyday planning questions: how much to make, who does what, when to do it, and which route to drive.\nYou describe the question once, press Solve, check the answer and apply it."),
        },
        {
            target: ".o_or_dashboard .o_or_kpis",
            title: _t("The dashboard"),
            text: _t("Your numbers at a glance: how many problems you have, how many times they were solved, and how many answers are waiting."),
        },
        {
            target: ".o_or_kpi_to_apply",
            title: _t("Answers waiting for you"),
            text: _t("'To Apply' counts answers that are ready but not yet written into your records. Click it to see them."),
        },
        {
            target: ".o_or_recent",
            title: _t("Recent runs"),
            text: _t("Every time someone presses Solve, a run is saved here with its result. Click a line to open it."),
        },
        {
            target: ".o_main_navbar [data-menu-xmlid='operations_research.or_menu_problems']",
            title: _t("Problems"),
            text: _t("A problem is a saved question, for example 'How much glass should we produce this week?'. You build it once and solve it again whenever your data changes."),
        },
        {
            target: ".o_main_navbar [data-menu-xmlid='operations_research.or_menu_runs']",
            title: _t("Runs"),
            text: _t("The history of all answers: when, by whom, and whether they were applied."),
        },
        {
            target: ".o_or_tutorials_button",
            optional: true,
            title: _t("Tutorials"),
            text: _t("Come back here any time for videos and guided tours like this one."),
        },
        {
            title: _t("Who can do what"),
            text: _t("Users solve problems and look at the answers.\nDesigners also create and change problems.\nManagers also apply answers to the records.\nSet this per person in Settings > Users."),
        },
    ],
});

guideRegistry.add("how_much", {
    sequence: 2,
    name: _t("Build a 'How much?' problem"),
    video: "operations_research_for_odoo.mp4#t=19",
    description: _t("Decide quantities (production mix, budget split, which projects to pick) within your limits."),
    icon: "fa-sliders",
    steps: () => [
        {
            before: (h) => h.openRecord("or.problem", [["id", "=", 0]], { newContext: NEW_LP }),
            title: _t("A new problem"),
            text: _t("We will build a question like: 'How many of each product should we make this week to earn the most, without overloading the machines?'"),
        },
        {
            target: field("name"),
            title: _t("1. Give it a name"),
            text: _t("Use a name everyone understands, e.g. 'Weekly Production Plan'."),
        },
        {
            target: field("problem_type"),
            title: _t("2. Type of question"),
            text: _t("'Linear / Integer Program' answers 'how much?' questions. Other types: Assignment (who does what), Scheduling (when) and Vehicle Routing (which route)."),
        },
        {
            target: field("model_id"),
            title: _t("3. Which records?"),
            text: _t("Choose the records the answer is about, for example Products. Any model works, including your custom ones."),
        },
        {
            target: ".o_field_widget[name=domain]",
            optional: true,
            title: _t("4. Filter them (optional)"),
            text: _t("Click 'Edit Domain' to keep only some records, e.g. only products of type 'Product'. The number shows how many records take part."),
        },
        {
            before: openTab("lp_decision"),
            target: field("var_field_id"),
            title: _t("5. What should be decided?"),
            text: _t("Pick the field the solver fills in for every record, e.g. 'Planned Qty'. Nothing is written until you click Apply."),
        },
        {
            target: field("var_type"),
            title: _t("6. Kind of number"),
            text: _t("Continuous allows decimals (kilos, hours). Integer gives whole pieces. Yes / No picks or skips each record."),
        },
        {
            target: field("objective_sense"),
            title: _t("7. Your goal"),
            text: _t("Maximize (profit, output) or Minimize (cost, waste)."),
        },
        {
            target: field("objective_field_id"),
            title: _t("8. What to count"),
            text: _t("The value per unit, e.g. 'Unit Margin'. The solver maximizes the total: margin x quantity, summed over all records."),
        },
        {
            target: field("ub_field_id"),
            title: _t("9. Limits per record"),
            text: _t("Optional minimum and maximum, from a field or a fixed number. Example: never plan more than the customer demand."),
        },
        {
            before: openTab("lp_constraints"),
            target: ".o_field_widget[name=constraint_ids]",
            title: _t("10. Factory limits"),
            text: _t("Add one line per limit with 'Add a line'. Example: machine hours used must be at most the machine capacity, one rule per machine. Each line is summarized in plain words."),
        },
        {
            before: openTab("solver"),
            target: field("time_limit"),
            title: _t("11. Time limit"),
            text: _t("How long the solver may search. 30 seconds is plenty for most problems; it stops earlier when it has the best answer."),
        },
        {
            target: "button[name=action_run]",
            title: _t("12. Solve"),
            text: _t("Click Solve. Small problems are answered in seconds; big ones run in the background and you get a message when ready.\nNext tour: review and apply the answer."),
        },
    ],
});

guideRegistry.add("review_apply", {
    sequence: 3,
    name: _t("Review and apply an answer"),
    description: _t("Read the result, find the bottleneck, and write the plan into your records safely."),
    icon: "fa-check-square-o",
    steps: () => [
        {
            before: async (h) => {
                const found = await h.openRecord("or.run", [
                    ["state", "in", ["done", "applied"]],
                    ["has_solution", "=", true],
                ]);
                h.ctx.noRun = !found;
                if (!found) {
                    await h.openAction(PROBLEM_ACTION);
                }
            },
            when: (ctx) => !ctx.noRun,
            title: _t("A solved run"),
            text: _t("Each time you press Solve you get a run like this one. Let's read it."),
        },
        {
            when: (ctx) => ctx.noRun,
            title: _t("Nothing solved yet"),
            text: _t("Open a problem and press Solve first, then start this tour again to learn how to read and apply the answer."),
        },
        {
            when: (ctx) => !ctx.noRun,
            target: field("solver_status"),
            title: _t("Status"),
            text: _t("Optimal = the best possible answer.\nFeasible = a good answer, not proven best.\nInfeasible = your rules can't all be met (the Conflicts tab tells you which one).\nUnbounded = a limit is missing."),
        },
        {
            when: (ctx) => !ctx.noRun,
            target: field("objective_value"),
            title: _t("The result"),
            text: _t("The value of your goal with this plan, e.g. the total margin."),
        },
        {
            when: (ctx) => !ctx.noRun,
            before: openTab("results"),
            target: ".o_field_widget[name=line_ids]",
            title: _t("Proposed changes"),
            text: _t("For every record: current value, proposed value and the difference. Bold lines will change. Click the arrow icon to open a record."),
        },
        {
            when: (ctx) => !ctx.noRun,
            before: openTab("constraints"),
            target: ".o_field_widget[name=constraint_result_ids]",
            optional: true,
            title: _t("Limits used"),
            text: _t("How much of each limit is used. 'Bottleneck' ticked = fully used: this is what stops you from doing more.\nFor decimal problems, 'Shadow Price' shows what one extra unit of that limit is worth."),
        },
        {
            when: (ctx) => !ctx.noRun,
            target: "button[name=action_apply]",
            optional: true,
            title: _t("Apply"),
            text: _t("Happy with the answer? A Manager clicks Apply to write it into the records. If someone changed a record after solving, you get a warning instead of losing their work."),
        },
        {
            when: (ctx) => !ctx.noRun,
            target: ".o-mail-Chatter || .o-mail-Form-chatter",
            optional: true,
            title: _t("History"),
            text: _t("The chatter on the right keeps who solved and who applied, and when."),
        },
    ],
});

guideRegistry.add("assignment", {
    sequence: 4,
    name: _t("Who does what? (Assignment)"),
    description: _t("Give each order, task or student to the best line, person or class."),
    icon: "fa-users",
    requires: "assignment",
    steps: () => [
        {
            before: (h) =>
                h.openRecord("or.problem", [["problem_type", "=", "assignment"]], {
                    newContext: { default_problem_type: "assignment" },
                }),
            title: _t("Assignment problems"),
            text: _t("Each record (e.g. an order) gets exactly one resource (e.g. a production line), at the lowest cost and without overloading anyone."),
        },
        {
            before: openTab("assignment"),
            target: field("assign_field_id"),
            title: _t("Where the answer goes"),
            text: _t("A field on your records pointing to the resource, e.g. 'Assigned Line'. The resources are the records of that field."),
        },
        {
            target: field("assign_cost_mode"),
            title: _t("Cost of each choice"),
            text: _t("Same for all, a resource field (e.g. hourly rate), or record field x resource field (e.g. hours x rate)."),
        },
        {
            target: field("assign_capacity_field_id"),
            title: _t("Capacity"),
            text: _t("How much each resource can take, and how much each record needs (the load). Empty = one record per resource."),
        },
        {
            target: field("assign_task_match_field_id"),
            title: _t("Matching rules"),
            text: _t("Optional: e.g. the required skill must equal the line's skill. Mismatches can be forbidden or simply cost extra."),
        },
        {
            target: field("assign_all"),
            title: _t("Must everyone be assigned?"),
            text: _t("If capacity is short, untick this: some records stay unassigned (at a penalty) instead of 'no solution'."),
        },
        {
            target: "button[name=action_run]",
            title: _t("Solve and review"),
            text: _t("Solve, then check 'Proposed Resource' for every record and Apply."),
        },
    ],
});

guideRegistry.add("scheduling", {
    sequence: 5,
    name: _t("When? (Scheduling and Gantt)"),
    video: "operations_research_for_odoo.mp4#t=353",
    description: _t("Plan jobs on machines or people in the right order and on time."),
    icon: "fa-tasks",
    requires: "scheduling",
    steps: () => [
        {
            before: (h) =>
                h.openRecord("or.problem", [["problem_type", "=", "scheduling"]], {
                    newContext: { default_problem_type: "scheduling" },
                }),
            title: _t("Scheduling problems"),
            text: _t("Give each job a start and end time so that a machine never does two jobs at once, jobs wait for the ones before them, and deadlines are met."),
        },
        {
            before: openTab("scheduling"),
            target: field("sched_duration_field_id"),
            title: _t("Duration"),
            text: _t("How long each job takes, in minutes, hours or days."),
        },
        {
            target: field("sched_resource_field_id"),
            title: _t("Machine or person"),
            text: _t("Jobs on the same machine never overlap."),
        },
        {
            target: field("sched_predecessor_field_id"),
            title: _t("Order of work"),
            text: _t("Jobs listed here must finish first, e.g. cutting before tempering."),
        },
        {
            target: field("sched_deadline_mode"),
            optional: true,
            title: _t("Deadlines"),
            text: _t("Soft: try to be on time, late jobs are penalized by priority.\nHard: must be on time, otherwise there is no solution."),
        },
        {
            target: field("sched_objective"),
            title: _t("Goal"),
            text: _t("Finish everything early, be late as little as possible, or finish important jobs first."),
        },
        {
            target: field("sched_start_field_id"),
            title: _t("Where the plan goes"),
            text: _t("The date fields that receive the planned start and end when you Apply."),
        },
        {
            before: (h) => h.openRecord("or.run", [["problem_type", "=", "scheduling"], ["has_solution", "=", true]]),
            target: "button[name=action_open_gantt]",
            optional: true,
            title: _t("The Gantt chart"),
            text: _t("After solving, the Gantt button shows the plan per machine."),
        },
        {
            before: (h) => h.click("button[name=action_open_gantt]"),
            target: ".o_or_gantt_grid",
            optional: true,
            title: _t("Reading the Gantt"),
            text: _t("One row per machine. Purple = new times, gray = unchanged, red = late. Click a bar to open the job."),
        },
    ],
});

guideRegistry.add("routing", {
    sequence: 6,
    name: _t("Which route? (Delivery routing)"),
    description: _t("Plan truck routes with capacities, delivery windows and the shortest driving."),
    icon: "fa-truck",
    video: "operations_research_for_odoo.mp4#t=204",
    requires: "routing",
    steps: () => [
        {
            before: (h) =>
                h.openRecord("or.problem", [["problem_type", "=", "routing"]], {
                    newContext: { default_problem_type: "routing" },
                }),
            title: _t("Routing problems"),
            text: _t("Decide which truck visits which customer, and in which order, driving as little as possible."),
        },
        {
            before: openTab("routing"),
            target: field("route_location_mode"),
            title: _t("Where are the stops?"),
            text: _t("From the customer's location on the map (latitude and longitude on the contact) or from two fields."),
        },
        {
            target: field("route_demand_field_id"),
            title: _t("Load"),
            text: _t("How much each stop needs (pallets, boxes). Trucks never carry more than their capacity."),
        },
        {
            target: field("route_depot_id"),
            title: _t("Depot and departure"),
            text: _t("Where trucks start and end, and when they leave. Speed and road factor turn distances into driving times."),
        },
        {
            target: ".o_field_widget[name=route_vehicle_ids]",
            title: _t("Your trucks"),
            text: _t("One line per truck: capacity, maximum km, and the record written on the deliveries (e.g. the driver)."),
        },
        {
            target: field("route_allow_drop"),
            title: _t("Too many stops?"),
            text: _t("Tick 'Allow Skipping Stops' to get the best partial plan instead of 'no solution'."),
        },
        {
            before: (h) => h.openRecord("or.run", [["problem_type", "=", "routing"], ["has_solution", "=", true]]),
            target: "button[name=action_open_route_map]",
            optional: true,
            title: _t("The map"),
            text: _t("After solving, the Map button draws the routes."),
        },
        {
            before: (h) => h.click("button[name=action_open_route_map]"),
            target: ".o_or_route_canvas",
            optional: true,
            title: _t("Reading the map"),
            text: _t("One color per truck, numbers show the stop order, D is the depot. Click a truck on the right to highlight its route."),
        },
    ],
});

guideRegistry.add("whatif_automation", {
    sequence: 7,
    name: _t("What-if scenarios and automatic runs"),
    description: _t("Compare options side by side, and let problems solve themselves on a schedule."),
    icon: "fa-code-fork",
    steps: () => [
        {
            before: (h) => h.openRecord("or.problem", [["parent_id", "=", false]], { newContext: NEW_LP }),
            target: "button[name=action_create_scenario]",
            title: _t("What if...?"),
            text: _t("'New Scenario' copies the problem so you can change a limit (e.g. add an extra shift) without touching the original."),
        },
        {
            target: "button[name=action_compare_scenarios] || button[name=action_view_runs]",
            title: _t("Compare"),
            text: _t("Solve each scenario, then 'Compare Scenarios' lists their results side by side so you can choose."),
        },
        {
            target: "button[name=action_queue]",
            title: _t("Solve in Background"),
            text: _t("For big problems: keep working while it solves. You get a message in Discuss when the answer is ready."),
        },
        {
            before: openTab("schedule"),
            target: field("schedule_active"),
            title: _t("Run automatically"),
            text: _t("Solve every hour, day, week or month, e.g. plan tomorrow's routes every night."),
        },
        {
            target: field("schedule_auto_apply") + " || " + field("schedule_user_id"),
            optional: true,
            title: _t("Apply automatically (Managers)"),
            text: _t("Optional: write the answer automatically when it is the best possible one and nothing changed since solving."),
        },
        {
            title: _t("You're ready"),
            text: _t("Settings (Configuration menu) let a manager change the default time limit and the size limits. Happy optimizing!"),
        },
    ],
});
