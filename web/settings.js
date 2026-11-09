import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// The key goes straight to the ComfyUI server. It is deliberately not stored as a
// ComfyUI setting, so it never ends up in the browser's settings data.

function describe(key) {
  if (!key.set) return "No key saved";
  return key.source === "environment"
    ? `Using OPENROUTER_API_KEY (ends ${key.last4})`
    : `Key saved (ends ${key.last4})`;
}

async function save(value, status, input) {
  status.textContent = "Saving...";
  try {
    const response = await api.fetchApi("/openrouter_suite/key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: value }),
    });
    const result = await response.json();
    status.textContent = describe(result.key);
    input.value = "";
  } catch (error) {
    status.textContent = "Could not save the key";
  }
}

function renderKeyField() {
  const root = document.createElement("div");
  root.style.cssText = "display:flex;flex-direction:column;gap:6px;min-width:280px";

  const row = document.createElement("div");
  row.style.cssText = "display:flex;gap:6px";

  const input = document.createElement("input");
  input.type = "password";
  input.placeholder = "sk-or-...";
  input.autocomplete = "off";
  input.style.cssText = "flex:1;padding:4px 8px";

  const saveButton = document.createElement("button");
  saveButton.textContent = "Save";
  const clearButton = document.createElement("button");
  clearButton.textContent = "Clear";

  const status = document.createElement("div");
  status.style.cssText = "font-size:12px;opacity:0.75";

  saveButton.onclick = () => input.value.trim() && save(input.value.trim(), status, input);
  clearButton.onclick = () => save("", status, input);

  row.append(input, saveButton, clearButton);
  root.append(row, status);

  api.fetchApi("/openrouter_suite/status")
    .then((response) => response.json())
    .then((result) => { status.textContent = describe(result.key); })
    .catch(() => { status.textContent = "Could not read key status"; });

  return root;
}

app.registerExtension({
  name: "OpenRouterSuite.Settings",
  settings: [
    {
      id: "OpenRouterSuite.ApiKey",
      category: ["OpenRouter", "Account", "API key"],
      name: "OpenRouter API key",
      tooltip: "Stored on the ComfyUI server, never in workflows.",
      type: renderKeyField,
      defaultValue: "",
    },
  ],
});
