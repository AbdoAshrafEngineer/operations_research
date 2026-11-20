import { registry } from "@web/core/registry";

const next = (counter) => [
    { content: `bubble ${counter}`, trigger: `.o_or_guide_counter:contains("${counter}")` },
    { content: "next", trigger: ".o_or_guide_next:enabled", run: "click" },
];

registry.category("web_tour.tours").add("or_tutorials_tour", {
    steps: () => [
        { content: "tutorial cards", trigger: ".o_or_tutorial_how_much .o_or_tutorial_start" },
        { content: "video available", trigger: ".o_or_tutorial_routing .o_or_tutorial_video" },
        { content: "start guided tour", trigger: ".o_or_tutorial_how_much .o_or_tutorial_start", run: "click" },
        ...next("1 / 13"),
        ...next("2 / 13"),
        ...next("3 / 13"),
        { content: "spotlight on a real field", trigger: ".o_or_guide_hole" },
        { content: "auto-play", trigger: ".o_or_guide_play", run: "click" },
        { content: "progress bar", trigger: ".o_or_guide_progress" },
        { content: "close", trigger: ".o_or_guide_close", run: "click" },
        { content: "closed", trigger: "body:not(:has(.o_or_guide))" },
    ],
});

registry.category("web_tour.tours").add("or_gantt_map_tour", {
    steps: () => [
        { trigger: "button[name=action_open_gantt]", run: "click" },
        { trigger: ".o_or_gantt .o_or_gantt_bar" },
        { trigger: ".o_or_gantt button.btn-secondary", run: "click" },
        { trigger: "button[name=action_open_gantt]" },
    ],
});

registry.category("web_tour.tours").add("or_map_tour", {
    steps: () => [
        { trigger: "button[name=action_open_route_map]", run: "click" },
        { trigger: ".o_or_route_map svg polyline" },
        { trigger: ".o_or_route_card", run: "click" },
        { trigger: ".o_or_route_card.border-primary" },
    ],
});

registry.category("web_tour.tours").add("or_dashboard_tour", {
    steps: () => [
        { trigger: ".o_or_dashboard .o_or_kpi_problems .o_or_kpi_value" },
        { trigger: ".o_or_dashboard .o_or_tutorials_button", run: "click" },
        { trigger: ".o_or_tutorials .o_or_tutorial" },
    ],
});
