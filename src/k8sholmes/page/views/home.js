import { api, el, emptyState, environmentPicker, currentEnv, hasRole, state, toast } from "./common.js";

const STARTERS = [
  "Why is api restarting?",
  "Which services appear unused or obsolete?",
  "Which important alerts are missing?",
  "What reliability or security practices are we overlooking?",
  "Is this environment isolated from external networks?",
  "What changed before this problem started?",
  "What could be affected if api fails?",
  "Did the latest change resolve the issue?",
];

export async function renderHome(main) {
  const envId = currentEnv();
  if (!envId) {
    main.replaceChildren(el("div", { class: "stack", style: "max-width:720px;margin:48px auto" },
      el("h1", {}, "What would you like to investigate?"),
      emptyState("No environment is connected yet.", state.me && state.me.is_admin ? el("a", { href: "#/settings/environments" }, "Connect an environment in Settings") : el("span", { class: "muted" }, "Ask an administrator to connect one and grant you access.")),
    ));
    return;
  }
  const models = await api("GET", "/api/models");
  const env = state.environments.find((e) => e.id === envId);
  const defaultModel = models.endpoints.find((m) => m.id === (env && env.default_model_id)) || models.endpoints.find((m) => m.is_default);
  const question = el("textarea", { placeholder: "Ask in plain words…", "aria-label": "Your question" });
  const namespaces = el("input", { type: "text", placeholder: "namespaces, comma-separated (empty: every namespace you may read)", "aria-label": "Namespaces" });
  const modelSelect = el("select", { "aria-label": "Model" });
  for (const m of models.endpoints) modelSelect.append(el("option", { value: m.id, selected: defaultModel && m.id === defaultModel.id }, `${m.name} · ${m.model}`));
  const canAsk = hasRole(envId, "investigator") && models.endpoints.length > 0;
  const ask = async () => {
    const q = question.value.trim();
    if (!q) { question.focus(); return; }
    button.disabled = true;
    try {
      const made = await api("POST", "/api/investigations", {
        environment_id: envId, question: q, namespaces: namespaces.value.split(",").map((s) => s.trim()).filter(Boolean),
        model_endpoint_id: modelSelect.value || null, idempotency_key: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      });
      location.hash = `#/investigations/${made.investigation.id}`;
    } catch (err) { toast(err.message, "error"); button.disabled = false; }
  };
  const button = el("button", { class: "btn primary", onclick: ask, disabled: !canAsk }, "Investigate");
  question.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) ask(); });
  main.replaceChildren(el("div", { class: "stack", style: "max-width:760px;margin:32px auto" },
    el("h1", {}, "What would you like to investigate?"),
    el("div", { class: "ask" }, question, button),
    el("div", { class: "chips", "aria-label": "Starting questions" }, STARTERS.map((s) => el("button", { class: "chip", type: "button", onclick: () => { question.value = s; question.focus(); } }, s))),
    el("div", { class: "row small muted" },
      el("span", {}, "Environment"), environmentPicker(() => renderHome(main)),
      el("span", {}, "Model"), modelSelect,
    ),
    namespaces,
    !hasRole(envId, "investigator") ? el("div", { class: "notice" }, "You can read this environment's investigations but not start one: that needs the investigator role.") : null,
    models.endpoints.length === 0 ? el("div", { class: "notice" }, "No model endpoint is configured. ", el("a", { href: "#/settings/models" }, "Add one in Settings"), " or run the demo.") : null,
    el("div", { class: "faint xs" }, "The model frames the question and chooses capabilities; what may run is decided by your role and the environment's settings, never by the model. Every finding cites its evidence."),
  ));
  question.focus();
}
