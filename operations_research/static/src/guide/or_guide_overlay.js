import { Component, onWillDestroy, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { findVisible, stepDuration } from "./or_guide_service";

const BUBBLE_W = 380;
const MARGIN = 14;

export class OrGuideOverlay extends Component {
    static template = "operations_research.GuideOverlay";
    static props = {};

    setup() {
        this.guide = useService("or_guide");
        this.state = useState(this.guide.state);
        this.layout = useState({ hole: null, bubble: "", tick: 0 });
        // follow the target while the page scrolls, resizes or re-renders
        this.interval = setInterval(() => this.measure(), 150);
        onWillDestroy(() => clearInterval(this.interval));
    }

    get step() {
        return this.state.steps[this.state.index] || {};
    }

    get isLast() {
        return this.state.index === this.state.steps.length - 1;
    }

    get progressStyle() {
        return `animation-duration: ${stepDuration(this.step)}ms;`;
    }

    /** Rectangle of the target; form fields also include their label. */
    targetRect(el) {
        const rect = el.getBoundingClientRect();
        const cell = el.closest(".o_wrap_input");
        const label = cell?.previousElementSibling;
        if (!label || !label.classList.contains("o_wrap_label")) {
            return rect;
        }
        const lr = label.getBoundingClientRect();
        const cr = cell.getBoundingClientRect();
        const left = Math.min(lr.left, cr.left);
        const top = Math.min(lr.top, cr.top);
        const right = Math.max(lr.right, cr.right);
        const bottom = Math.max(lr.bottom, cr.bottom);
        return { left, top, width: right - left, height: bottom - top };
    }

    measure() {
        if (!this.state.active) {
            return;
        }
        const el = !this.state.busy && findVisible(this.state.target);
        if (!el) {
            this.layout.hole = null;
            this.layout.bubble = "";
            return;
        }
        const r = this.targetRect(el);
        const pad = 6;
        const hole = {
            top: Math.max(r.top - pad, 2),
            left: Math.max(r.left - pad, 2),
            width: Math.min(r.width + 2 * pad, window.innerWidth - 4),
            height: Math.min(r.height + 2 * pad, window.innerHeight - 4),
        };
        const holeStyle = `top:${hole.top}px;left:${hole.left}px;width:${hole.width}px;height:${hole.height}px;`;
        // bubble below the target if there is room, otherwise above, otherwise beside
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        let left = Math.min(Math.max(hole.left, MARGIN), vw - BUBBLE_W - MARGIN);
        let pos;
        if (hole.top + hole.height + 230 < vh) {
            pos = `top:${hole.top + hole.height + MARGIN}px;left:${left}px;`;
        } else if (hole.top > 260) {
            pos = `bottom:${vh - hole.top + MARGIN}px;left:${left}px;`;
        } else {
            left = hole.left + hole.width + MARGIN + BUBBLE_W < vw ? hole.left + hole.width + MARGIN : MARGIN;
            pos = `top:${Math.max(MARGIN, Math.min(hole.top, vh - 260))}px;left:${left}px;`;
        }
        if (holeStyle !== this.layout.hole) {
            this.layout.hole = holeStyle;
        }
        if (pos !== this.layout.bubble) {
            this.layout.bubble = pos;
        }
    }
}

registry.category("main_components").add("OrGuideOverlay", { Component: OrGuideOverlay });
