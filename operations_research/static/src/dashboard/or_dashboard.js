import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";

const STATUS_CLASS = {
    optimal: "text-bg-success",
    feasible: "text-bg-info",
    not_solved: "text-bg-warning",
    infeasible: "text-bg-danger",
    unbounded: "text-bg-danger",
    abnormal: "text-bg-danger",
    model_invalid: "text-bg-danger",
};

export class OrDashboard extends Component {
    static template = "operations_research.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, days: 30 });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.data = await this.orm.call("or.run", "get_or_dashboard_data", [], {
            days: this.state.days,
        });
    }

    async setDays(days) {
        this.state.days = days;
        await this.load();
    }

    get cards() {
        const k = this.state.data.kpis;
        return [
            { key: "problems", label: _t("Problems"), value: k.problems, icon: "fa-sitemap", open: () => this.openProblems() },
            { key: "runs", label: _t("Runs"), value: k.runs, icon: "fa-cogs", open: () => this.openRuns([], _t("Runs")) },
            { key: "to_apply", label: _t("To Apply"), value: k.to_apply, icon: "fa-check-square-o", highlight: k.to_apply > 0,
              open: () => this.openRuns([["state", "=", "done"], ["has_solution", "=", true]], _t("To Apply")) },
            { key: "in_progress", label: _t("In Progress"), value: k.in_progress, icon: "fa-spinner",
              open: () => this.openRuns([["state", "in", ["queued", "running"]]], _t("In Progress")) },
            { key: "success_rate", label: _t("Success Rate"), value: `${k.success_rate}%`, icon: "fa-trophy" },
            { key: "failed", label: _t("Failed / No Solution"), value: k.failed, icon: "fa-exclamation-triangle", danger: k.failed > 0,
              open: () => this.openRuns(["|", ["state", "=", "failed"], ["solver_status", "in", ["infeasible", "unbounded", "not_solved", "abnormal"]]], _t("Failed / No Solution")) },
            { key: "avg_time", label: _t("Avg. Solve Time (s)"), value: k.avg_time, icon: "fa-clock-o" },
            { key: "scenarios", label: _t("Scenarios"), value: k.scenarios, icon: "fa-code-fork" },
        ];
    }

    get statusTotal() {
        return this.state.data.status.reduce((sum, s) => sum + s.count, 0) || 1;
    }

    statusClass(key) {
        return STATUS_CLASS[key] || "text-bg-secondary";
    }

    formatDate(value) {
        return value ? formatDateTime(deserializeDateTime(value)) : "";
    }

    openTutorials() {
        this.action.doAction("operations_research.or_tutorials_action");
    }

    openProblems() {
        this.action.doAction("operations_research.or_problem_action");
    }

    openProblem(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "or.problem", res_id: id, views: [[false, "form"]] });
    }

    openRun(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "or.run", res_id: id, views: [[false, "form"]] });
    }

    openRuns(domain, name) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "or.run",
            domain,
            views: [[false, "list"], [false, "form"]],
        });
    }
}

registry.category("actions").add("operations_research.dashboard", OrDashboard);
