import { Component, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { guideRegistry } from "../guide/or_guide_service";

export class OrVideoDialog extends Component {
    static template = "operations_research.VideoDialog";
    static components = { Dialog };
    static props = ["title", "src", "close"];
}

export class OrTutorials extends Component {
    static template = "operations_research.Tutorials";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.guide = useService("or_guide");
        this.dialog = useService("dialog");
        this.state = useState({ tutorials: [] });
        onWillStart(async () => {
            const fields = await this.orm.call("or.problem", "fields_get", [["problem_type"]], {
                attributes: ["selection"],
            });
            const types = fields.problem_type.selection.map(([value]) => value);
            this.state.tutorials = guideRegistry
                .getEntries()
                .map(([key, guide]) => ({ key, ...guide }))
                .filter((guide) => !guide.requires || types.includes(guide.requires))
                .sort((a, b) => (a.sequence || 99) - (b.sequence || 99));
        });
    }

    videoSrc(tutorial) {
        return `/operations_research/static/description/videos/${tutorial.video}`;
    }

    watch(tutorial) {
        this.dialog.add(OrVideoDialog, { title: tutorial.name, src: this.videoSrc(tutorial) });
    }

    startTour(tutorial, autoplay = false) {
        this.guide.start(tutorial.key, { autoplay });
    }
}

registry.category("actions").add("operations_research.tutorials", OrTutorials);
