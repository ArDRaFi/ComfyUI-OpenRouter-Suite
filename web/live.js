import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Nodes whose model dropdown is a DynamicCombo capture their options when created, so a
// node already on the canvas only sees new models once the workflow is reopened.
const DYNAMIC_NODES = new Set(["ORS_Image", "ORS_Video", "ORS_TextToSpeech"]);

function toast(detail) {
  app.extensionManager?.toast?.add({ severity: "info", summary: "OpenRouter", detail, life: 6000 });
}

async function reloadDefinitions() {
  if (typeof app.reloadNodeDefs === "function") {
    await app.reloadNodeDefs();
  } else {
    await app.refreshComboInNodes();
  }
}

async function onCatalogChanged({ detail }) {
  await reloadDefinitions();
  const added = detail?.added ?? [];
  if (added.length === 0) return;
  const label = added.length === 1 ? `New OpenRouter model: ${added[0]}` : `${added.length} new OpenRouter models`;
  const placed = (app.graph?.nodes ?? []).some((node) => DYNAMIC_NODES.has(node.type));
  toast(placed ? `${label}. Reopen the workflow to use them on existing nodes.` : label);
}

app.registerExtension({
  name: "OpenRouterSuite.Live",
  setup() {
    api.addEventListener("ors.catalog", onCatalogChanged);
    api.fetchApi("/openrouter_suite/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ if_stale: true }),
    }).catch(() => {});
  },
});
