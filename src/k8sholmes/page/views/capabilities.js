import { api, el, emptyState, environmentPicker, currentEnv, hasRole, kv, ms, toast, when, coverageBadge } from "./common.js";

export async function renderCapabilities(main) {
  const envId = currentEnv();
  const data = await api("GET", `/api/capabilities${envId ? `?env=${encodeURIComponent(envId)}` : ""}`);
  let filter = "";
  let selected = null;
  const root = el("div", { class: "stack" });
  main.replaceChildren(root);

  function list() {
    const rows = data.capabilities.filter((c) => !filter || `${c.id} ${c.title} ${c.purpose}`.toLowerCase().includes(filter));
    if (!rows.length) return emptyState("No capability matches.");
    return el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Id"), el("th", {}, "Class"), el("th", {}, "Purpose"), el("th", {}, "Systems"), envId ? el("th", {}, "Here") : null)),
      el("tbody", {}, rows.map((c) => el("tr", { style: "cursor:pointer", class: selected && selected.id === c.id ? "selected" : "", onclick: () => { selected = c; draw(); } },
        el("td", { class: "mono" }, c.id), el("td", {}, el("span", { class: `badge ${c.class === "inspect" ? "info" : c.class === "probe" ? "review" : "severe"}` }, c.class)), el("td", {}, el("div", {}, c.title), el("div", { class: "muted xs" }, c.purpose.slice(0, 140) + (c.purpose.length > 140 ? "…" : ""))), el("td", { class: "muted xs" }, c.systems.join(", ")),
        envId ? el("td", {}, c.available ? el("span", { class: "badge ok" }, "available") : el("span", { class: "badge", title: c.why_not }, "unavailable")) : null))));
  }

  function inspector() {
    if (!selected) return el("div", { class: "inspector muted small" }, "Select a capability to see what it accesses, its inputs, its coverage and its history.");
    const c = selected;
    const node = el("div", { class: "inspector stack-sm" },
      el("h2", { class: "mono" }, c.id), el("p", {}, c.purpose),
      kv([["class", c.class], ["systems", c.systems.join(", ")], ["permissions", c.permissions.join(", ") || "none"], ["coverage it states", c.coverage.map((x) => coverageBadge(x))], ["origin", c.origin], envId ? ["here", c.available ? "available" : c.why_not] : null].filter(Boolean)),
      el("h3", {}, "Inputs"), el("pre", {}, JSON.stringify(c.inputs.properties || {}, null, 1)),
    );
    if (envId && hasRole(envId, "operator")) {
      const settings = c.settings || { enabled: true, namespaces: [], max_items: 500, max_log_bytes: 262144, timeout_ms: 20000 };
      node.append(el("h3", {}, "Settings in this environment"),
        el("form", { class: "stack-sm", onsubmit: async (e) => { e.preventDefault(); const fd = new FormData(e.target); try { await api("PATCH", `/api/environments/${envId}/capabilities/${c.id}`, { enabled: fd.get("enabled") === "on", namespaces: String(fd.get("namespaces") || "").split(",").map((s) => s.trim()).filter(Boolean), max_items: Number(fd.get("max_items")), max_log_bytes: Number(fd.get("max_log_bytes")), timeout_ms: Number(fd.get("timeout_ms")) }); toast("Saved."); await reload(); } catch (err) { toast(err.message, "error"); } } },
          el("label", { class: "toggle" }, el("input", { type: "checkbox", name: "enabled", checked: settings.enabled }), "enabled"),
          el("div", { class: "field" }, el("label", {}, "namespace allow-list (comma-separated; empty: the scope's)"), el("input", { type: "text", name: "namespaces", value: settings.namespaces.join(", ") })),
          el("div", { class: "row" }, el("div", { class: "field" }, el("label", {}, "max items"), el("input", { type: "number", name: "max_items", value: settings.max_items })), el("div", { class: "field" }, el("label", {}, "max log bytes"), el("input", { type: "number", name: "max_log_bytes", value: settings.max_log_bytes })), el("div", { class: "field" }, el("label", {}, "timeout ms"), el("input", { type: "number", name: "timeout_ms", value: settings.timeout_ms }))),
          el("button", { class: "btn sm", type: "submit" }, "Save settings")));
    }
    if (envId && hasRole(envId, "investigator")) {
      const inputs = el("textarea", { "aria-label": "Test inputs as JSON", style: "min-height:72px" }, JSON.stringify(sampleInputs(c), null, 1));
      const out = el("div", {});
      node.append(el("h3", {}, "Test before enabling"), inputs, el("button", { class: "btn sm", onclick: async () => { out.replaceChildren(el("div", { class: "muted small" }, "Running…")); try { const r = await api("POST", `/api/capabilities/${c.id}/test`, { environment_id: envId, inputs: JSON.parse(inputs.value || "{}") }); out.replaceChildren(el("div", { class: "stack-sm" }, el("div", { class: "row" }, el("span", { class: `badge ${r.call.status === "completed" ? "ok" : "severe"}` }, r.call.status), r.evidence ? coverageBadge(r.evidence.coverage) : null, el("span", { class: "faint xs" }, ms(r.call.duration_ms))), r.call.reason ? el("div", { class: "notice error" }, r.call.reason) : null, r.evidence ? el("div", { class: "small" }, r.evidence.summary) : null, r.evidence ? el("pre", {}, typeof r.evidence.data === "string" ? r.evidence.data.slice(0, 4000) : JSON.stringify(r.evidence.data, null, 1).slice(0, 4000)) : null)); } catch (err) { out.replaceChildren(el("div", { class: "notice error" }, err.message)); } } }, "Run test"), out);
      const history = el("div", { class: "muted small" }, "Loading history…");
      node.append(el("h3", {}, "Execution history"), history);
      api("GET", `/api/capabilities/${c.id}/calls?env=${encodeURIComponent(envId)}`).then((h) => {
        history.replaceChildren(h.calls.length ? el("table", {}, el("tbody", {}, h.calls.map((x) => el("tr", {}, el("td", {}, el("span", { class: `badge ${x.status === "completed" ? "ok" : x.status === "refused" ? "review" : "severe"}` }, x.status)), el("td", { class: "mono xs" }, JSON.stringify(x.inputs).slice(0, 80)), el("td", { class: "num faint" }, ms(x.duration_ms)), el("td", { class: "faint" }, when(x.at)), el("td", {}, x.investigation_id ? el("a", { href: `#/investigations/${x.investigation_id}` }, "investigation") : "test"))))) : el("span", {}, "Never run here."));
      }).catch((err) => history.replaceChildren(el("div", { class: "notice error" }, err.message)));
    }
    return node;
  }

  function sampleInputs(c) {
    const out = {};
    for (const [name, prop] of Object.entries(c.inputs.properties || {})) {
      if (prop.default !== undefined) out[name] = prop.default;
      else if ((c.inputs.required || []).includes(name)) out[name] = prop.type === "integer" || prop.type === "number" ? 1 : prop.type === "array" ? [] : name === "namespace" ? "shop" : name === "kind" ? "deployment" : "api";
    }
    return out;
  }

  async function reload() { Object.assign(data, await api("GET", `/api/capabilities?env=${encodeURIComponent(envId)}`)); selected = data.capabilities.find((c) => c.id === selected.id) || null; draw(); }

  function draw() {
    root.replaceChildren(
      el("div", { class: "row between" }, el("h1", {}, "Capabilities"), el("div", { class: "row small muted" }, envId ? ["Environment", environmentPicker(() => renderCapabilities(main))] : null, el("input", { type: "text", placeholder: "Search…", "aria-label": "Search capabilities", value: filter, oninput: (e) => { filter = e.target.value.toLowerCase(); draw(); } }))),
      el("p", { class: "muted small" }, "What an investigation can do. Each capability has a purpose, validated inputs, a class (inspect reads, probe sends traffic, change alters infrastructure), the systems it reaches and the coverage it can state. Whether it may run is decided from your role and the environment's settings, never by the model."),
      Object.keys(data.broken_extensions || {}).length ? el("div", { class: "notice error" }, "Broken extensions: ", Object.entries(data.broken_extensions).map(([k, v]) => `${k}: ${v}`).join("; ")) : null,
      el("div", { class: "grid-main" }, list(), inspector()),
    );
  }
  draw();
}
