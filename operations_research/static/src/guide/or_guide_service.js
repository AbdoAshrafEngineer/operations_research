import { proxy } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

/** Registry of guided tours: key -> { name, description, icon, video, requires, steps() } */
export const guideRegistry = registry.category("or_guides");

const WAIT_MS = 6000;

export function findVisible(selector) {
    if (!selector) {
        return null;
    }
    for (const part of selector.split("||")) {
        for (const el of document.querySelectorAll(part.trim())) {
            const rect = el.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                return el;
            }
        }
    }
    return null;
}

export async function waitFor(selector, timeout = WAIT_MS) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
        const el = findVisible(selector);
        if (el) {
            return el;
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return null;
}

/** Duration a step stays on screen in auto-play mode (ms). */
export function stepDuration(step) {
    const words = `${step.title || ""} ${step.text || ""}`.split(/\s+/).length;
    return Math.min(13000, Math.max(4500, 2200 + words * 330));
}

export const orGuideService = {
    dependencies: ["action", "orm", "notification"],
    start(env, { action, orm, notification }) {
        const state = proxy({
            active: false,
            key: null,
            name: "",
            steps: [],
            index: 0,
            busy: false,
            autoplay: false,
            target: null,
            token: 0,
        });
        let timer = null;

        const helpers = {
            action,
            orm,
            ctx: {},
            async openAction(xmlid) {
                await action.doAction(xmlid, { clearBreadcrumbs: true });
            },
            /** Open the latest record matching domain, or a new form when none exists. */
            async openRecord(model, domain, { newContext = null } = {}) {
                const ids = await orm.search(model, domain, { limit: 1, order: "id desc" });
                if (!ids.length && !newContext) {
                    return false;
                }
                await action.doAction(
                    {
                        type: "ir.actions.act_window",
                        res_model: model,
                        res_id: ids[0],
                        views: [[false, "form"]],
                        context: ids.length ? {} : newContext,
                    },
                    { clearBreadcrumbs: true }
                );
                return true;
            },
            async click(selector) {
                const el = await waitFor(selector);
                if (el) {
                    el.click();
                    await new Promise((resolve) => setTimeout(resolve, 350));
                }
                return Boolean(el);
            },
        };

        function clearTimer() {
            if (timer) {
                clearTimeout(timer);
                timer = null;
            }
        }

        function schedule() {
            clearTimer();
            if (state.active && state.autoplay && !state.busy) {
                const step = state.steps[state.index];
                timer = setTimeout(() => {
                    if (state.index < state.steps.length - 1) {
                        go(state.index + 1, 1);
                    } else {
                        state.autoplay = false;
                    }
                }, stepDuration(step));
            }
        }

        async function go(index, direction = 1) {
            clearTimer();
            const token = ++state.token;
            state.busy = true;
            state.target = null;
            let i = index;
            while (i >= 0 && i < state.steps.length) {
                const step = state.steps[i];
                state.index = i;
                if (step.when && !step.when(helpers.ctx)) {
                    i += direction >= 0 ? 1 : -1;
                    continue;
                }
                try {
                    if (step.before && direction >= 0) {
                        await step.before(helpers);
                    }
                } catch (error) {
                    console.warn("Guide step preparation failed", error);
                }
                if (token !== state.token) {
                    return; // user moved on meanwhile
                }
                if (!step.target) {
                    break;
                }
                const el = await waitFor(step.target, step.optional ? 2500 : WAIT_MS);
                if (token !== state.token) {
                    return;
                }
                if (el) {
                    el.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
                    state.target = step.target;
                    break;
                }
                if (!step.optional) {
                    break; // show the bubble centered rather than getting stuck
                }
                i += direction >= 0 ? 1 : -1;
            }
            if (i >= state.steps.length) {
                stop(); // every remaining step was skipped: the tour is over
                return;
            }
            if (i < 0) {
                go(0, 1);
                return;
            }
            state.index = i;
            state.busy = false;
            schedule();
        }

        function start(key, { autoplay = false } = {}) {
            if (!guideRegistry.contains(key)) {
                notification.add(_t("This guided tour is not available."), { type: "warning" });
                return;
            }
            const guide = guideRegistry.get(key);
            helpers.ctx = {};
            Object.assign(state, {
                active: true,
                key,
                name: guide.name,
                steps: guide.steps(),
                index: 0,
                autoplay,
            });
            go(0);
        }

        function stop() {
            clearTimer();
            state.token++;
            Object.assign(state, { active: false, key: null, steps: [], target: null, autoplay: false });
        }

        return {
            state,
            start,
            stop,
            next: () => (state.index < state.steps.length - 1 ? go(state.index + 1, 1) : stop()),
            back: () => state.index > 0 && go(state.index - 1, -1),
            toggleAutoplay() {
                state.autoplay = !state.autoplay;
                schedule();
            },
        };
    },
};

registry.category("services").add("or_guide", orGuideService);
