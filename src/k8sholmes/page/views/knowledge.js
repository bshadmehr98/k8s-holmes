import { api, el, emptyState, environmentPicker, currentEnv, hasRole, form, toast, when, resourceText } from "./common.js";

export async function renderKnowledge(main) {
  const envId = currentEnv();
  if (!envId) { main.replaceChildren(emptyState("No environment yet.")); return; }
  let tab = "exceptions";
  let filter = "active";
  const data = await api("GET", `/api/knowledge?env=${encodeURIComponent(envId)}`);
  const root = el("div", { class: "stack" });
  main.replaceChildren(root);
  const can = hasRole(envId, "investigator");

  function exceptions() {
    const now = Date.now();
    const status = (x) => x.revoked_at ? "revoked" : x.expires_at && new Date(x.expires_at).getTime() < now ? "expired" : x.review_at && new Date(x.review_at).getTime() < now ? "due" : "active";
    const rows = data.exceptions.filter((x) => filter === "all" || status(x) === filter || (filter === "active" && status(x) === "due"));
    const chips = el("div", { class: "chips" }, ["active", "due", "expired", "revoked", "all"].map((f) => el("button", { class: "chip", "aria-pressed": filter === f, style: filter === f ? "border-color:var(--accent);color:var(--text-1)" : "", onclick: () => { filter = f; draw(); } }, f)));
    if (!rows.length) return el("div", { class: "stack-sm" }, chips, emptyState("No exceptions here. Accept a finding as intentional from an investigation, and it appears here with its scope, explanation and history."));
    return el("div", { class: "stack-sm" }, chips, el("div", { class: "list" }, rows.map((x) => el("div", { class: "list-item" },
      el("div", { class: "stack-sm" },
        el("div", { class: "row" }, el("span", { class: `badge ${status(x) === "active" ? "ok" : status(x) === "due" ? "review" : ""}` }, status(x)), el("span", { class: "mono xs" }, x.condition), el("span", {}, resourceText(x.scope) + (x.applies_to === "namespace" ? " (whole namespace)" : ""))),
        el("div", {}, x.explanation),
        el("div", { class: "byline" }, `approved by ${x.approved_by} · ${when(x.approved_at)}${x.review_at ? ` · review ${x.review_at.slice(0, 10)}` : ""}${x.expires_at ? ` · expires ${x.expires_at.slice(0, 10)}` : ""}${x.reconsider_when ? ` · reconsider when: ${x.reconsider_when}` : ""}${x.triggers.length ? ` · triggers: ${x.triggers.join(", ")}` : ""}${x.revoked_at ? ` · revoked by ${x.revoked_by}: ${x.revocation_reason}` : ""}`),
        x.revisions.length ? el("details", {}, el("summary", { class: "xs" }, `${x.revisions.length} revision${x.revisions.length > 1 ? "s" : ""}`), el("ul", { class: "small muted" }, x.revisions.map((r) => el("li", {}, `${when(r.at)} · ${r.author} · ${r.change}`)))) : null,
      ),
      can && !x.revoked_at ? el("div", { class: "stack-sm" },
        el("details", {}, el("summary", { class: "small" }, "Edit"), form([{ name: "explanation", label: "Explanation", type: "textarea", value: x.explanation }, { name: "review_at", label: "Review on", type: "date", value: x.review_at ? x.review_at.slice(0, 10) : "" }, { name: "expires_at", label: "Expires on", type: "date", value: x.expires_at ? x.expires_at.slice(0, 10) : "" }, { name: "reconsider_when", label: "Reconsider when", value: x.reconsider_when }], async (d) => { await api("PATCH", `/api/exceptions/${x.id}`, { explanation: d.explanation, review_at: d.review_at || null, expires_at: d.expires_at || null, reconsider_when: d.reconsider_when }); await reload(); }, "Save revision")),
        el("button", { class: "btn sm danger", onclick: async () => { const reason = prompt("Why is this exception revoked?"); if (reason === null) return; try { await api("POST", `/api/exceptions/${x.id}/revoke`, { reason }); await reload(); } catch (err) { toast(err.message, "error"); } } }, "Revoke"),
      ) : null,
    ))));
  }

  function notes() {
    const list = data.knowledge.length ? el("div", { class: "list" }, data.knowledge.map((n) => el("div", { class: "list-item" },
      el("div", { class: "stack-sm" }, el("div", { class: "row" }, el("span", { class: "badge" }, n.kind), el("span", { class: "muted small" }, n.scope ? resourceText(n.scope) : "whole environment")), el("div", {}, n.text), el("div", { class: "byline" }, `${n.author} · ${when(n.updated_at)} · source: ${n.source}${n.revisions.length ? ` · ${n.revisions.length} revisions` : ""}`)),
      can ? el("details", {}, el("summary", { class: "small" }, "Edit"), form([{ name: "text", label: "Text", type: "textarea", value: n.text }], async (d) => { await api("PATCH", `/api/knowledge/${n.id}`, { text: d.text }); await reload(); }, "Save revision")) : null,
    ))) : emptyState("No knowledge notes yet. Record what the team knows: expected traffic, criticality and ownership, maintenance windows, constraints, dependencies, lessons.");
    const add = can ? el("details", {}, el("summary", {}, "Add a note"), form([
      { name: "kind", label: "Kind", type: "select", options: [["ownership", "ownership"], ["criticality", "criticality"], ["traffic", "expected traffic"], ["maintenance", "maintenance window"], ["constraint", "architectural constraint"], ["dependency", "known dependency"], ["lesson", "lesson from an investigation"]] },
      { name: "kind_ref", label: "Resource kind (optional)", placeholder: "Deployment" }, { name: "namespace", label: "Namespace (optional)", placeholder: "shop" }, { name: "name", label: "Name (optional)", placeholder: "api" },
      { name: "text", label: "The note", type: "textarea", required: true },
    ], async (d) => { await api("POST", "/api/knowledge", { environment_id: envId, kind: d.kind, text: d.text, scope: d.kind_ref || d.namespace || d.name ? { kind: d.kind_ref || "", namespace: d.namespace || null, name: d.name || "" } : null }); await reload(); }, "Add")) : null;
    return el("div", { class: "stack-sm" }, el("p", { class: "muted small" }, "User-provided knowledge is kept apart from observed facts. The model sees it as context, never as evidence, and findings say when it influenced them."), add, list);
  }

  async function reload() { Object.assign(data, await api("GET", `/api/knowledge?env=${encodeURIComponent(envId)}`)); draw(); }

  function draw() {
    root.replaceChildren(
      el("div", { class: "row between" }, el("h1", {}, "Knowledge"), el("div", { class: "row small muted" }, "Environment", environmentPicker(() => renderKnowledge(main)))),
      el("div", { class: "tabs", role: "tablist" }, [["exceptions", `Accepted exceptions (${data.exceptions.length})`], ["notes", `Knowledge notes (${data.knowledge.length})`]].map(([k, label]) => el("button", { role: "tab", "aria-selected": tab === k, onclick: () => { tab = k; draw(); } }, label))),
      tab === "exceptions" ? exceptions() : notes(),
    );
  }
  draw();
}
