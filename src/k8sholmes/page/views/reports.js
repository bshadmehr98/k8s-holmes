import { api, el, emptyState, environmentPicker, currentEnv, hasRole, form, toast, when, kv } from "./common.js";

const KIND_LABEL = { investigation: "investigation report", incident_review: "incident review", reliability_review: "reliability and monitoring review", comparison: "comparison", live: "live summary" };

export async function renderReports(main, id) {
  if (id) return renderReport(main, id);
  const envId = currentEnv();
  if (!envId) { main.replaceChildren(emptyState("No environment yet.")); return; }
  const data = await api("GET", `/api/reports?env=${encodeURIComponent(envId)}`);
  const can = hasRole(envId, "investigator");
  const list = data.reports.length ? el("div", { class: "list" }, data.reports.map((r) => el("div", { class: "list-item link", tabindex: 0, role: "link", onclick: () => { location.hash = `#/reports/${r.id}`; }, onkeydown: (e) => { if (e.key === "Enter") location.hash = `#/reports/${r.id}`; } },
    el("div", {}, el("div", { class: "title" }, r.title), el("div", { class: "meta" }, `${KIND_LABEL[r.kind] || r.kind} · revision ${r.revision} · ${when(r.generated_at)} · by ${r.generated_by}${r.edits.length ? ` · ${r.edits.length} edit${r.edits.length > 1 ? "s" : ""}` : ""}`)),
    el("span", { class: "badge" }, r.kind.replace("_", " ")),
  ))) : emptyState("No reports yet. Generate one from an investigation's Report tab, or a reliability review here.");
  const review = can ? el("details", {}, el("summary", {}, "Generate a reliability and monitoring review"),
    el("p", { class: "muted small" }, "Reads the safeguards and alert rules of the scope through the gate, then counts by code: probes, limits, replicas, disruption budgets, workloads no alert references. Accepted exceptions are applied and listed."),
    form([{ name: "namespaces", label: "Namespaces (comma-separated; empty for every namespace you may read)", placeholder: "shop" }], async (d) => {
      const made = await api("POST", "/api/reports", { environment_id: envId, kind: "reliability_review", namespaces: d.namespaces.split(",").map((s) => s.trim()).filter(Boolean) });
      location.hash = `#/reports/${made.report.id}`;
    }, "Generate")) : null;
  main.replaceChildren(el("div", { class: "stack" },
    el("div", { class: "row between" }, el("h1", {}, "Reports"), el("div", { class: "row small muted" }, "Environment", environmentPicker(() => renderReports(main)))),
    el("p", { class: "muted small" }, "Every report is built by code from stored records: each number is a count of the records it names, and a person's edits are kept apart from the generated text."),
    review,
    list,
  ));
}

async function renderReport(main, id) {
  let data = await api("GET", `/api/reports/${id}`);
  const root = el("div", { class: "stack" });
  main.replaceChildren(root);
  const can = () => hasRole(data.report.environment_id, "investigator");

  async function reload() { data = await api("GET", `/api/reports/${id}`); draw(); }

  function shares() {
    const active = data.shares.filter((s) => !s.revoked_at);
    const rows = data.shares.length ? el("div", { class: "list" }, data.shares.map((s) => el("div", { class: "list-item" },
      el("div", { class: "small" }, el("span", { class: `badge ${s.revoked_at ? "" : new Date(s.expires_at) < Date.now() ? "review" : "ok"}` }, s.revoked_at ? "revoked" : new Date(s.expires_at) < Date.now() ? "expired" : "active"), ` created ${when(s.created_at)} by ${s.created_by} · expires ${s.expires_at.slice(0, 10)}`),
      can() && !s.revoked_at ? el("button", { class: "btn sm danger", onclick: async () => { try { await api("POST", `/api/shares/${s.id}/revoke`); await reload(); } catch (err) { toast(err.message, "error"); } } }, "Revoke") : null,
    ))) : el("div", { class: "muted small" }, "No share links.");
    const make = can() ? form([{ name: "days", label: "Days until the link expires", type: "number", value: 7 }], async (d) => {
      const made = await api("POST", `/api/reports/${id}/shares`, { days: Number(d.days) });
      const url = `${location.origin}/${made.path.replace(/^\//, "")}`;
      try { await navigator.clipboard.writeText(url); toast("Share link copied. It is shown once; it opens this report only, read-only."); } catch { toast(`Share link (shown once): ${url}`); }
      await reload();
    }, "Create a view-only link") : null;
    return el("details", {}, el("summary", {}, `Share links (${active.length} active)`), el("div", { class: "stack-sm" }, el("p", { class: "muted small" }, "A share link opens this report without signing in, read-only, until it expires or is revoked. Creation and revocation are audited."), make, rows));
  }

  function draw() {
    const r = data.report;
    root.replaceChildren(
      el("div", { class: "row between" },
        el("div", {}, el("h1", {}, r.title), el("div", { class: "muted small" }, `${KIND_LABEL[r.kind] || r.kind} · revision ${r.revision} of ${data.revisions.length} · generated ${when(r.generated_at)} by ${r.generated_by}`)),
        el("div", { class: "row" }, el("a", { class: "btn quiet", href: `/api/reports/${id}/markdown`, target: "_blank" }, "Markdown"), el("a", { class: "btn quiet", href: `/api/reports/${id}/export`, download: `${id}.json` }, "JSON")),
      ),
      data.revisions.length > 1 ? el("div", { class: "chips" }, data.revisions.map((v) => el("a", { class: "chip", href: `#/reports/${v.id}`, style: v.id === id ? "border-color:var(--accent);color:var(--text-1)" : "" }, `revision ${v.revision}${v.edits ? ` (${v.edits} edits)` : ""}`))) : null,
      renderReportBody(r, can() ? async (section, text) => { await api("POST", `/api/reports/${id}/edits`, { section, text }); await reload(); } : null),
      el("div", { class: "section" }, el("h3", {}, "Numbers"), el("p", { class: "muted small" }, "Each number is the count of the stored records it names."), kv(Object.entries(r.numbers).map(([k, v]) => [k.replaceAll("_", " "), String(v)]))),
      shares(),
    );
  }
  draw();
}

export function renderReportBody(report, onEdit) {
  return el("div", { class: "stack" }, report.sections.map((section) => {
    const edits = report.edits.filter((e) => e.section === section.heading);
    return el("div", { class: "section" },
      el("h2", {}, section.heading),
      sectionBody(section),
      ...edits.map((e) => el("div", { class: "notice", style: "border-color:var(--accent)" }, el("div", { class: "byline" }, `Edit by ${e.author} · ${when(e.at)}`), el("div", {}, e.text))),
      onEdit ? el("details", {}, el("summary", { class: "small" }, "Add an edit to this section"), form([{ name: "text", label: "Your addition or correction (kept apart from the generated text, with your byline)", type: "textarea", required: true }], async (d) => onEdit(section.heading, d.text), "Add edit")) : null,
    );
  }));
}

function sectionBody(section) {
  const body = section.body;
  if (section.kind === "text") return kv(Object.entries(body || {}).filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && !v.length)).map(([k, v]) => [k.replaceAll("_", " "), Array.isArray(v) ? v.join(", ") : String(v)]));
  if (section.kind === "list") return body.length ? el("ul", { class: "small" }, body.map((item) => el("li", {}, String(item)))) : el("div", { class: "muted small" }, "Nothing.");
  if (section.kind === "findings") return body.length ? el("div", { class: "list" }, body.map((f) => el("div", { class: "list-item" }, el("div", { class: "stack-sm" },
    el("div", { class: "row" }, el("span", { class: "badge" }, f.kind), el("span", { class: "badge" }, f.state.replace("_", " ")), el("span", { class: "title" }, f.title), el("span", { class: "mono xs muted" }, f.condition)),
    el("div", { class: "small" }, f.explanation),
    el("div", { class: "byline" }, `by ${f.author} · evidence: ${f.evidence_for.map((e) => `${e.id}${e.missing ? " (missing)" : ` (${e.source}, ${e.coverage}, ${e.collected_at ? e.collected_at.slice(0, 16) : ""})`}`).join("; ") || "none"}${f.knowledge_used.length ? ` · influenced by knowledge ${f.knowledge_used.join(", ")}` : ""}`),
  )))) : el("div", { class: "muted small" }, "None.");
  if (!body.length) return el("div", { class: "muted small" }, "None.");
  const keys = Object.keys(body[0]);
  return el("div", { style: "overflow:auto" }, el("table", {}, el("thead", {}, el("tr", {}, keys.map((k) => el("th", {}, k.replaceAll("_", " "))))), el("tbody", {}, body.map((row) => el("tr", {}, keys.map((k) => el("td", { class: typeof row[k] === "number" ? "num" : "" }, cell(row[k]))))))));
}

function cell(v) {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) return v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ");
  if (typeof v === "object") return v.kind && v.name !== undefined ? `${v.kind}${v.namespace ? ` ${v.namespace}/` : " "}${v.name}` : JSON.stringify(v);
  return String(v);
}

export async function renderShared(main, token) {
  let data;
  try { data = await api("GET", `/api/shared/${encodeURIComponent(token)}`); } catch (err) {
    main.replaceChildren(el("div", { class: "state" }, err.status === 404 ? "This share link does not exist, expired or was revoked." : err.message));
    return;
  }
  const r = data.report;
  document.title = `${r.title} · K8sHolmes`;
  main.replaceChildren(el("div", { class: "stack", style: "max-width:960px;margin:0 auto" },
    el("div", { class: "row between" }, el("div", {}, el("h1", {}, r.title), el("div", { class: "muted small" }, `${KIND_LABEL[r.kind] || r.kind} · revision ${r.revision} · generated ${when(r.generated_at)} by ${r.generated_by} · shared read-only`)), el("a", { class: "btn quiet", href: `/api/shared/${encodeURIComponent(token)}/markdown`, target: "_blank" }, "Markdown")),
    renderReportBody(r, null),
    el("div", { class: "section" }, el("h3", {}, "Numbers"), kv(Object.entries(r.numbers).map(([k, v]) => [k.replaceAll("_", " "), String(v)]))),
  ));
}
