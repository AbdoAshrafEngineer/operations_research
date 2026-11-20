import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";

const TICKS = 6;
const SHORT = { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" };

export class OrGantt extends Component {
    static template = "operations_research.Gantt";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, error: null });
        this.runId = this.props.action?.params?.run_id;
        onWillStart(() => this.load());
    }

    async load() {
        if (!this.runId) {
            this.state.error = _t("Open a scheduling run and click Gantt.");
            return;
        }
        const data = await this.orm.call("or.run", "get_gantt_data", [[this.runId]]);
        if (!data.start) {
            this.state.error = _t("This run has no planned tasks.");
            return;
        }
        this.start = deserializeDateTime(data.start);
        this.end = deserializeDateTime(data.end);
        // avoid a zero-width scale when all tasks have no duration
        this.span = Math.max(this.end.toMillis() - this.start.toMillis(), 60 * 1000);
        this.state.data = data;
    }

    get ticks() {
        const ticks = [];
        for (let i = 0; i <= TICKS; i++) {
            const millis = this.start.toMillis() + (this.span * i) / TICKS;
            // first/last labels hug the edges, the others are centered (class flipped by rtlcss)
            let style = `inset-inline-start: ${(100 * i) / TICKS}%;`;
            let cls = "o_or_gantt_tick_mid";
            if (i === 0) {
                style = "inset-inline-start: 0;";
                cls = "";
            } else if (i === TICKS) {
                style = "inset-inline-end: 0;";
                cls = "";
            }
            ticks.push({
                key: i,
                style,
                cls,
                label: this.start.plus({ milliseconds: millis - this.start.toMillis() }).toLocaleString(SHORT),
            });
        }
        return ticks;
    }

    barStyle(task) {
        const start = deserializeDateTime(task.start).toMillis();
        const end = deserializeDateTime(task.end).toMillis();
        const left = (100 * (start - this.start.toMillis())) / this.span;
        const width = Math.max((100 * (end - start)) / this.span, 0.5);
        return `inset-inline-start: ${left}%; width: ${width}%;`;
    }

    barClass(task) {
        if (task.late_minutes > 0) {
            return "o_or_gantt_bar_late";
        }
        return task.changed ? "o_or_gantt_bar_changed" : "o_or_gantt_bar_same";
    }

    tooltip(task) {
        const late = task.late_minutes > 0 ? ` · ${_t("Late")} ${task.late_minutes} min` : "";
        return `${task.name}: ${formatDateTime(deserializeDateTime(task.start))} → ${formatDateTime(
            deserializeDateTime(task.end)
        )}${late}`;
    }

    openTask(task) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: task.res_model,
            res_id: task.res_id,
            views: [[false, "form"]],
        });
    }

    openRun() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "or.run",
            res_id: this.runId,
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("operations_research.gantt", OrGantt);
