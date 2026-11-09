import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Shows the last run's cost in the node title. Progress text cannot be used for this,
// because ComfyUI clears it when the job ends.
const COST_SUFFIX = /  \[(?:\$|cost n\/a)[^\]]*\]$/;

function findNode(id) {
  if (id == null) return null;
  const local = String(id).split(":").pop();
  return app.graph?.getNodeById?.(id) ?? app.canvas?.graph?.getNodeById?.(local) ?? null;
}

app.registerExtension({
  name: "OpenRouterSuite.Cost",
  setup() {
    api.addEventListener("ors.cost", ({ detail }) => {
      const node = findNode(detail?.node);
      if (!node) return;
      const title = node.title ?? node.type;
      node.title = `${title.replace(COST_SUFFIX, "")}  [${detail.text}]`;
      node.setDirtyCanvas?.(true, true);
    });
  },
});
