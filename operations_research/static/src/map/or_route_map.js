import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";

const WIDTH = 1000;
const HEIGHT = 600;
const PADDING = 40;

/** Offline route map: equirectangular projection drawn in SVG (no tile server). */
export class OrRouteMap extends Component {
    static template = "operations_research.RouteMap";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, error: null, selected: null });
        this.runId = this.props.action?.params?.run_id;
        this.width = WIDTH;
        this.height = HEIGHT;
        onWillStart(() => this.load());
    }

    async load() {
        if (!this.runId) {
            this.state.error = _t("Open a routing run and click Map.");
            return;
        }
        const data = await this.orm.call("or.run", "get_route_map_data", [[this.runId]]);
        const points = [data.depot, ...data.dropped];
        for (const route of data.routes) {
            points.push(...route.stops);
        }
        const lats = points.map((p) => p.lat);
        const lngs = points.map((p) => p.lng);
        this.bounds = {
            minLat: Math.min(...lats),
            maxLat: Math.max(...lats),
            minLng: Math.min(...lngs),
            maxLng: Math.max(...lngs),
        };
        this.cosLat = Math.cos(((this.bounds.minLat + this.bounds.maxLat) / 2) * (Math.PI / 180));
        const spanX = Math.max((this.bounds.maxLng - this.bounds.minLng) * this.cosLat, 1e-6);
        const spanY = Math.max(this.bounds.maxLat - this.bounds.minLat, 1e-6);
        this.scale = Math.min((WIDTH - 2 * PADDING) / spanX, (HEIGHT - 2 * PADDING) / spanY);
        // center the drawing in the canvas
        this.offsetX = (WIDTH - spanX * this.scale) / 2;
        this.offsetY = (HEIGHT - spanY * this.scale) / 2;
        this.state.data = data;
    }

    x(point) {
        return this.offsetX + (point.lng - this.bounds.minLng) * this.cosLat * this.scale;
    }

    y(point) {
        return this.offsetY + (this.bounds.maxLat - point.lat) * this.scale;
    }

    path(route) {
        const depot = this.state.data.depot;
        const pts = [depot, ...route.stops, depot];
        return pts.map((p) => `${this.x(p).toFixed(1)},${this.y(p).toFixed(1)}`).join(" ");
    }

    isDimmed(route) {
        return this.state.selected !== null && this.state.selected !== route.id;
    }

    toggle(route) {
        this.state.selected = this.state.selected === route.id ? null : route.id;
    }

    formatEta(stop) {
        return stop.eta ? formatDateTime(deserializeDateTime(stop.eta)) : "";
    }

    formatEtaTime(stop) {
        return stop.eta
            ? deserializeDateTime(stop.eta).toLocaleString({ hour: "2-digit", minute: "2-digit" })
            : "";
    }

    openStop(stop) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: stop.res_model,
            res_id: stop.res_id,
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

registry.category("actions").add("operations_research.map", OrRouteMap);
