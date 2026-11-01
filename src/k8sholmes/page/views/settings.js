import { api, el, emptyState, form, kv, state, toast, when, ms } from "./common.js";

const TABS = [["environments", "Environments"], ["models", "Models"], ["users", "Users"], ["retention", "Retention"], ["audit", "Audit"]];

export async function renderSettings(main, tab = "environments") {
  if (!state.me || !state.me.is_admin) {
    main.replaceChildren(el("div", { class: "stack" }, el("h1", {}, "Settings"), el("div", { class: "state denied" }, "Settings are for administrators. Your roles: ", (state.me.grants || []).map((g) => `${g.role} in ${(state.environments.find((e) => e.id === g.environment_id) || {}).name || g.environment_id}`).join(", ") || "none")));
    return;
  }
  const root = el("div", { class: "stack" });
  main.replaceChildren(root);
  const body = el("div", {});
  root.append(el("h1", {}, "Settings"), el("div", { class: "tabs", role: "tablist" }, TABS.map(([k, label]) => el("a", { role: "tab", class: "btn quiet", "aria-selected": tab === k, href: `#/settings/${k}`, style: tab === k ? "color:var(--text-1);border-bottom:2px solid var(--accent);border-radius:0" : "border-radius:0" }, label))), body);
  const views = { environments, models, users, retention, audit };
  body.replaceChildren(await (views[tab] || environments)());

  async function environments() {
    const data = await api("GET", "/api/environments");
    const list = data.environments.length ? el("div", { class: "list" }, data.environments.map((e) => el("div", { class: "list-item" },
      el("div", { class: "stack-sm" },
        el("div", { class: "row" }, el("strong", {}, e.name), el("span", { class: "mono xs muted" }, e.recording ? "recording" : `context ${e.context}`), e.server_version ? el("span", { class: "badge ok" }, e.server_version) : el("span", { class: "badge review" }, "never connected")),
        el("div", { class: "muted small" }, e.connected_at ? `connected ${when(e.connected_at)} · ${Object.values(e.permissions).filter(Boolean).length} of ${Object.keys(e.permissions).length} permissions allowed` : "Connect to read the version and the permission matrix."),
        el("details", {}, el("summary", { class: "small" }, "Settings"), form([
          { name: "probes_enabled", label: "Probes enabled (active tests that send traffic; operators only)", type: "checkbox", value: e.settings.probes_enabled },
          { name: "changes_authorized_by", label: "Authorization path for changes", type: "select", value: e.settings.changes_authorized_by || "", options: [["", "none: change capabilities are refused"], ["operator_role", "an operator starts and confirms"], ["approval", "an operator requests, an admin approves"]] },
          { name: "default_model_id", label: "Default model endpoint id (optional)", value: e.settings.default_model_id || "" },
        ], async (d) => { await api("PATCH", `/api/environments/${e.id}/settings`, { probes_enabled: d.probes_enabled === "on", changes_authorized_by: d.changes_authorized_by || null, default_model_id: d.default_model_id || null }); toast("Saved."); renderSettings(main, "environments"); })),
        el("details", {}, el("summary", { class: "small" }, "Denied permissions"), el("div", { class: "mono xs muted" }, Object.entries(e.permissions).filter(([, v]) => !v).map(([k]) => k).join(", ") || "none")),
      ),
      el("div", { class: "stack-sm" }, el("button", { class: "btn sm", onclick: async () => { try { await api("POST", `/api/environments/${e.id}/connect`); toast("Connected."); renderSettings(main, "environments"); } catch (err) { toast(err.message, "error"); } } }, "Connect"), el("button", { class: "btn sm", onclick: async () => { try { const r = await api("POST", `/api/environments/${e.id}/inventory`); toast(`Inventory: ${r.inventory.items} items, ${r.inventory.relationships} relationships.`); } catch (err) { toast(err.message, "error"); } } }, "Take inventory")),
    ))) : emptyState("No environments. Add one below, or run `k8sholmes demo` for the recorded demo.");
    return el("div", { class: "stack" }, list, el("div", { class: "section" }, el("h2", {}, "Add an environment"), el("p", { class: "muted small" }, "A kubeconfig context on this host. Only the context's name is stored; kubectl reads the kubeconfig itself."), form([
      { name: "name", label: "Name", required: true, placeholder: "production" }, { name: "context", label: "kubeconfig context", required: true, placeholder: "kind-dev" }, { name: "kubeconfig", label: "kubeconfig path (optional)", placeholder: "~/.kube/config" },
    ], async (d) => { await api("POST", "/api/environments", { name: d.name, context: d.context, kubeconfig: d.kubeconfig || null }); state.me = undefined; location.reload(); }, "Add")));
  }

  async function models() {
    const data = await api("GET", "/api/models");
    const list = data.endpoints.length ? el("div", { class: "list" }, data.endpoints.map((m) => el("div", { class: "list-item" },
      el("div", { class: "stack-sm" },
        el("div", { class: "row" }, el("strong", {}, m.name), el("span", { class: "mono xs muted" }, m.model), m.is_default ? el("span", { class: "badge accent" }, "default") : null, m.tests.tool_calling ? el("span", { class: `badge ${m.tests.tool_calling.status === "passed" ? "ok" : "severe"}` }, m.tests.tool_calling.status === "passed" ? "usable" : "not usable: tool calling failed") : el("span", { class: "badge review" }, "untested")),
        el("div", { class: "muted xs mono" }, m.base_url, m.api_key_env ? ` · key from $${m.api_key_env}` : m.has_key ? " · key in the secrets file" : " · no key"),
        kv([["limits", `max ${m.limits.max_tokens} tokens/reply · ${ms(m.limits.timeout_ms)} timeout · ${m.limits.tokens_per_investigation} tokens/investigation · ${m.limits.tokens_per_day} tokens/day`], ["price", m.price ? `$${m.price.input_per_million}/M in · $${m.price.output_per_million}/M out` : "not set"], ["usage", `${m.usage.calls} calls · ${m.usage.prompt_tokens + m.usage.completion_tokens} tokens${m.usage.price != null ? ` · $${m.usage.price}` : ""}`]]),
        Object.keys(m.tests).length ? el("table", {}, el("tbody", {}, Object.entries(m.tests).map(([k, t]) => el("tr", {}, el("td", {}, k.replace("_", " ")), el("td", {}, el("span", { class: `badge ${t.status === "passed" ? "ok" : t.status === "failed" ? "severe" : "review"}` }, t.status)), el("td", { class: "muted xs" }, t.detail), el("td", { class: "faint xs" }, when(t.at)))))) : el("div", { class: "notice" }, "Not tested. Passing tests do not prove the model investigates well; they show it can connect, call tools, stream and answer in a schema."),
      ),
      el("div", { class: "stack-sm" }, m.id === "mdl_demo" ? null : el("button", { class: "btn sm", onclick: async (e) => { e.target.disabled = true; try { const r = await api("POST", `/api/models/${m.id}/test`); toast(`Tested: ${Object.values(r.tests).filter((t) => t.status === "passed").length} passed.`); renderSettings(main, "models"); } catch (err) { toast(err.message, "error"); e.target.disabled = false; } } }, "Run capability tests"), !m.is_default ? el("button", { class: "btn sm", onclick: async () => { await api("PATCH", `/api/models/${m.id}`, { is_default: true }); renderSettings(main, "models"); } }, "Make default") : null),
    ))) : emptyState("No model endpoints. Add your own OpenAI-compatible endpoint below.");
    return el("div", { class: "stack" }, list, el("div", { class: "section" }, el("h2", {}, "Add a model endpoint"), el("p", { class: "muted small" }, "Any OpenAI-compatible chat completions endpoint, including a private deployment. The key is kept in the home folder's secrets file (mode 0600) or read from an environment variable you name; it never reaches the database, a model message, a report or a log. Operational data goes only to the endpoint an investigation names; nothing is ever sent to another one."), form([
      { name: "name", label: "Name", required: true, placeholder: "team gateway" }, { name: "base_url", label: "Base URL", type: "url", required: true, placeholder: "https://gateway.example/v1" }, { name: "model", label: "Model", required: true, placeholder: "gpt-4.1" },
      { name: "api_key", label: "API key (stored in the secrets file)", type: "password" }, { name: "api_key_env", label: "…or the environment variable that holds it", placeholder: "OPENAI_API_KEY" },
      { name: "max_tokens", label: "Max tokens per reply", type: "number", value: 4096 }, { name: "timeout_ms", label: "Timeout (ms)", type: "number", value: 120000 }, { name: "tokens_per_investigation", label: "Token budget per investigation", type: "number", value: 400000 }, { name: "tokens_per_day", label: "Token budget per day", type: "number", value: 4000000 },
      { name: "input_per_million", label: "Price per million input tokens (optional)", type: "number", placeholder: "0" }, { name: "output_per_million", label: "Price per million output tokens (optional)", type: "number", placeholder: "0" },
    ], async (d) => { await api("POST", "/api/models", { name: d.name, base_url: d.base_url, model: d.model, api_key: d.api_key || null, api_key_env: d.api_key_env || null, limits: { max_tokens: Number(d.max_tokens), timeout_ms: Number(d.timeout_ms), tokens_per_investigation: Number(d.tokens_per_investigation), tokens_per_day: Number(d.tokens_per_day) }, price: d.input_per_million || d.output_per_million ? { input_per_million: Number(d.input_per_million || 0), output_per_million: Number(d.output_per_million || 0) } : null }); toast("Added. Run its capability tests before using it."); renderSettings(main, "models"); }, "Add")));
  }

  async function users() {
    const data = await api("GET", "/api/users");
    const envs = state.environments;
    const list = el("div", { class: "list" }, data.users.map((u) => el("div", { class: "list-item" },
      el("div", { class: "stack-sm" }, el("div", { class: "row" }, el("strong", {}, u.username), u.is_admin ? el("span", { class: "badge accent" }, "admin") : null), el("div", { class: "muted small" }, u.grants.length ? u.grants.map((g) => `${g.role} in ${(envs.find((e) => e.id === g.environment_id) || {}).name || g.environment_id}`).join(" · ") : "no environment access")),
      el("div", {}, el("details", {}, el("summary", { class: "small" }, "Grant a role"), form([{ name: "environment_id", label: "Environment", type: "select", options: envs.map((e) => [e.id, e.name]) }, { name: "role", label: "Role", type: "select", options: [["viewer", "viewer: reads"], ["investigator", "investigator: runs inspect capabilities, edits findings, accepts exceptions"], ["operator", "operator: also probes, proposals, verifications"], ["", "none: remove access"]] }], async (d) => { await api("PUT", `/api/users/${u.id}/grants`, { environment_id: d.environment_id, role: d.role || null }); renderSettings(main, "users"); }, "Set"))),
    )));
    return el("div", { class: "stack" }, list, el("div", { class: "section" }, el("h2", {}, "Add a user"), form([{ name: "username", label: "Username", required: true }, { name: "password", label: "Password (8 characters or more)", type: "password", required: true }, { name: "is_admin", label: "Administrator", type: "checkbox" }], async (d) => { await api("POST", "/api/users", { username: d.username, password: d.password, is_admin: d.is_admin === "on" }); renderSettings(main, "users"); }, "Add")));
  }

  async function retention() {
    const data = await api("GET", "/api/environments");
    return el("div", { class: "stack" }, el("p", { class: "muted small" }, "Per environment: how long short-lived evidence is kept, and how long investigations that nothing references are kept. Evidence cited by a reviewed finding, an exception or a report is never purged while referenced. The purge runs nightly and records what it removed in the audit log (milestone 3 brings the purge itself)."),
      ...data.environments.map((e) => el("div", { class: "section" }, el("h2", {}, e.name), form([{ name: "short_evidence_days", label: "Days to keep short-lived evidence", type: "number", value: e.settings.retention.short_evidence_days }, { name: "investigation_days", label: "Days to keep unreferenced investigations", type: "number", value: e.settings.retention.investigation_days }], async (d) => { await api("PATCH", `/api/environments/${e.id}/settings`, { retention: { short_evidence_days: Number(d.short_evidence_days), investigation_days: Number(d.investigation_days) } }); toast("Saved."); }))));
  }

  async function audit() {
    const data = await api("GET", "/api/audit");
    if (!data.entries.length) return emptyState("Nothing audited yet.");
    return el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "When"), el("th", {}, "Who"), el("th", {}, "Action"), el("th", {}, "Subject"), el("th", {}, "Details"))), el("tbody", {}, data.entries.map((a) => el("tr", {}, el("td", { class: "faint" }, when(a.at)), el("td", { class: "mono xs" }, a.user || "-"), el("td", {}, a.action), el("td", { class: "mono xs" }, a.subject || ""), el("td", { class: "muted xs" }, JSON.stringify(a.details).slice(0, 120))))));
  }
}
