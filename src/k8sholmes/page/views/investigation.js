import { api, el, kv, ms, statusBadge, coverageBadge, KIND_BADGE, STATE_BADGE, resourceText, hasRole, toast, when, form } from "./common.js";

let stream = null;

export async function renderInvestigation(main, id) {
  if (stream) { stream.close(); stream = null; }
  let bundle = await api("GET", `/api/investigations/${id}`);
  const inv = () => bundle.investigation;
  const envId = inv().environment_id;
  let tab = "findings";
  let selected = null; // {kind: "finding"|"call"|"evidence", id}

  const root = el("div", { class: "stack" });
  main.replaceChildren(root);

  function header() {
    const i = inv();
    const scope = (i.framing && i.framing.scope.namespaces.length ? i.framing.scope.namespaces : i.scope.namespaces).join(", ") || "every namespace";
    const current = bundle.steps.length ? bundle.steps[bundle.steps.length - 1] : null;
    const statusLine = i.status === "running" && current ? `${current.phase}: ${current.summary || "working…"}` : `${i.status}${i.limitations.length ? ` · ${i.limitations.length} limitation${i.limitations.length > 1 ? "s" : ""}` : ""}`;
    return el("div", { class: "stack-sm" },
      el("div", { class: "row between" },
        el("div", {}, el("h1", {}, i.question), el("div", { class: "muted small" }, `Scope: ${scope} · ${i.framing && i.framing.period.rule ? i.framing.period.rule : ""} · ${i.model_name || "no model yet"} · started ${when(i.created_at)}`)),
        el("div", { class: "row" }, el("span", { class: `dot ${i.status}` }), statusBadge(i.status)),
      ),
      el("div", { class: "small muted" }, statusLine),
    );
  }

  function answer() {
    const i = inv();
    const top = bundle.findings.filter((f) => f.state !== "accepted_risk")[0];
    if (i.answer) return el("div", { class: "answer" }, i.answer);
    if (top) return el("div", { class: "answer" }, top.title);
    if (i.status === "running" || i.status === "queued") return el("div", { class: "answer muted" }, "Investigating…");
    return el("div", { class: "answer muted" }, "No answer yet.");
  }

  function nextAction() {
    const i = inv();
    const row = el("div", { class: "row" });
    const can = hasRole(envId, "investigator");
    if (["running", "queued"].includes(i.status) && can) row.append(el("button", { class: "btn", onclick: () => act("cancel") }, "Cancel"));
    if (["cancelled", "interrupted", "partial", "failed"].includes(i.status) && can) row.append(el("button", { class: "btn primary", onclick: () => act("resume") }, "Resume"));
    if (["completed", "partial", "cancelled", "failed"].includes(i.status) && can) {
      const input = el("input", { type: "text", placeholder: "Add context or ask for another check…", "aria-label": "Follow-up", style: "min-width:320px" });
      const send = async () => { if (!input.value.trim()) return; await api("POST", `/api/investigations/${id}/follow_up`, { message: input.value.trim() }); input.value = ""; await refresh(); listen(); };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
      row.append(input, el("button", { class: "btn", onclick: send }, "Follow up"));
    }
    row.append(el("a", { class: "btn quiet", href: `/api/investigations/${id}/report`, target: "_blank" }, "Report"), el("a", { class: "btn quiet", href: `/api/investigations/${id}/export`, download: `${id}.json` }, "Download JSON"));
    return row;
  }

  async function act(what) {
    try { await api("POST", `/api/investigations/${id}/${what}`); await refresh(); if (what === "resume") listen(); } catch (err) { toast(err.message, "error"); }
  }

  function tabs() {
    const names = [["findings", `Findings (${bundle.findings.length})`], ["activity", `Activity (${bundle.steps.length} steps, ${bundle.calls.length} calls)`], ["evidence", `Evidence (${bundle.evidence.length})`], ["history", "History"]];
    return el("div", { class: "tabs", role: "tablist" }, names.map(([key, label]) => el("button", { role: "tab", "aria-selected": tab === key, onclick: () => { tab = key; draw(); } }, label)));
  }

  function findings() {
    if (!bundle.findings.length) return el("div", { class: "state" }, inv().status === "running" ? "Findings appear when the investigation concludes." : "No findings. See the limitations and the activity for what was read.");
    const open = bundle.findings.filter((f) => f.state !== "accepted_risk");
    const accepted = bundle.findings.filter((f) => f.state === "accepted_risk");
    const item = (f) => el("div", { class: `finding ${selected && selected.id === f.id ? "selected" : ""}`, tabindex: 0, onclick: () => { selected = { kind: "finding", id: f.id }; draw(); }, onkeydown: (e) => { if (e.key === "Enter") { selected = { kind: "finding", id: f.id }; draw(); } } },
      el("div", { class: "row" }, el("span", { class: `badge ${KIND_BADGE[f.kind] || ""}` }, f.kind), el("span", { class: `badge ${STATE_BADGE[f.state] || ""}` }, f.state.replace("_", " ")), el("span", { class: "title" }, f.title)),
      el("div", { class: "small muted" }, f.resources.map(resourceText).join(", "), " · ", el("span", { class: "mono xs" }, f.condition)),
    );
    const wrap = el("div", { class: "stack-sm" }, open.map(item));
    if (accepted.length) wrap.append(el("details", {}, el("summary", {}, `${accepted.length} accepted as intentional`), ...accepted.map(item)));
    if (inv().limitations.length) wrap.append(el("div", { class: "section" }, el("h3", {}, "Limitations"), el("ul", { class: "small muted" }, inv().limitations.map((l) => el("li", {}, l)))));
    return wrap;
  }

  function activity() {
    const callsByStep = {};
    for (const c of bundle.calls) (callsByStep[c.step_id] ||= []).push(c);
    return el("ul", { class: "timeline" }, bundle.steps.map((s) => el("li", {},
      el("div", { class: "phase" }, s.phase),
      el("div", {},
        el("div", {}, el("span", { class: `dot ${s.status === "completed" ? "completed" : s.status === "running" ? "running" : "failed"}` }), s.summary || (s.status === "running" ? "working…" : s.status), el("span", { class: "faint xs" }, ` · ${when(s.started_at)}`)),
        ...(callsByStep[s.id] || []).map((c) => el("div", { class: "call", onclick: () => { selected = { kind: "call", id: c.id }; draw(); } },
          el("span", { class: "mono" }, c.capability_id), el("span", { class: `badge ${c.status === "completed" ? "ok" : c.status === "refused" ? "review" : "severe"}` }, c.status), el("span", { class: "faint xs" }, ms(c.duration_ms)), c.reason ? el("span", { class: "muted xs" }, c.reason.slice(0, 120)) : null,
        )),
      ),
    )));
  }

  function evidence() {
    if (!bundle.evidence.length) return el("div", { class: "state" }, "Nothing has been read yet.");
    return el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Source"), el("th", {}, "Summary"), el("th", {}, "Coverage"), el("th", {}, "Redactions"), el("th", {}, "Read"))),
      el("tbody", {}, bundle.evidence.map((e) => el("tr", { style: "cursor:pointer", onclick: () => { selected = { kind: "evidence", id: e.id }; draw(); } },
        el("td", { class: "mono" }, e.source), el("td", {}, e.summary), el("td", {}, coverageBadge(e.coverage)), el("td", { class: "num" }, e.redactions || ""), el("td", { class: "faint" }, when(e.collected_at))))));
  }

  function history() {
    const thread = bundle.messages.filter((m) => m.role === "user" || (m.role === "assistant" && m.content));
    return el("div", { class: "stack" },
      el("div", {}, el("h3", {}, "Thread"), el("div", { class: "list" }, thread.map((m) => el("div", { class: "list-item" }, el("div", {}, el("div", { class: "byline" }, `${m.author} · round ${m.round} · ${when(m.at)}`), el("div", {}, m.content)))))),
      el("div", {}, el("h3", {}, "Finding revisions"), bundle.findings.some((f) => f.revisions.length) ? el("div", { class: "list" }, bundle.findings.flatMap((f) => f.revisions.map((r) => el("div", { class: "list-item" }, el("div", {}, el("div", { class: "byline" }, `${r.author} · ${when(r.at)}`), el("div", {}, `${f.title}: ${r.change}`), r.before && r.before.explanation ? el("div", { class: "muted small" }, `before: ${r.before.explanation}`) : null))))) : el("div", { class: "muted small" }, "No revisions.")),
      el("div", {}, el("h3", {}, "Model usage"), kv([["calls", inv().usage.model_calls], ["prompt tokens", inv().usage.prompt_tokens], ["completion tokens", inv().usage.completion_tokens], ["time", ms(inv().usage.duration_ms)], ["endpoint", inv().model_name]])),
    );
  }

  function inspector() {
    if (!selected) return el("div", { class: "inspector muted small" }, "Select a finding, a call or a piece of evidence to see its details here.");
    if (selected.kind === "finding") return findingInspector(bundle.findings.find((f) => f.id === selected.id));
    if (selected.kind === "call") {
      const c = bundle.calls.find((x) => x.id === selected.id);
      const ev = bundle.evidence.find((e) => e.id === c.evidence_id);
      return el("div", { class: "inspector stack-sm" }, el("h2", { class: "mono" }, c.capability_id), kv([["status", c.status], ["reason", c.reason], ["duration", ms(c.duration_ms)], ["at", when(c.at)], ["coverage", ev ? ev.coverage : ""]]), el("h3", {}, "Parameters"), el("pre", {}, JSON.stringify(c.inputs, null, 1)), ev ? el("button", { class: "btn sm", onclick: () => { selected = { kind: "evidence", id: ev.id }; draw(); } }, "Open the evidence") : null);
    }
    const e = bundle.evidence.find((x) => x.id === selected.id);
    return evidenceInspector(e);
  }

  function evidenceInspector(e) {
    const node = el("div", { class: "inspector stack-sm" }, el("h2", {}, e.summary || e.source), kv([["id", el("span", { class: "mono" }, e.id)], ["source", el("span", { class: "mono" }, e.source)], ["coverage", coverageBadge(e.coverage)], ["why", e.coverage_reason], ["read", when(e.collected_at)], ["sha256", el("span", { class: "mono xs" }, e.sha256.slice(0, 16) + "…")], ["truncated", e.truncated ? "yes" : "no"], ["redactions", e.redactions], ["error", e.error]]), el("h3", {}, "Query"), el("pre", {}, JSON.stringify(e.query, null, 1)), el("h3", {}, "Data"), el("div", { class: "muted small" }, "Loading…"));
    api("GET", `/api/evidence/${e.id}`).then((full) => { node.lastChild.replaceWith(el("pre", {}, typeof full.evidence.data === "string" ? full.evidence.data : JSON.stringify(full.evidence.data, null, 1))); }).catch((err) => { node.lastChild.replaceWith(el("div", { class: "notice error" }, err.message)); });
    return node;
  }

  function findingInspector(f) {
    const evLink = (eid) => { const e = bundle.evidence.find((x) => x.id === eid); return el("div", { class: "call", onclick: () => { selected = { kind: "evidence", id: eid }; draw(); } }, el("span", { class: "mono xs" }, eid), e ? el("span", { class: "muted xs" }, `${e.source} · `, coverageBadge(e.coverage)) : el("span", { class: "muted xs" }, "recalled from an earlier investigation")); };
    const can = hasRole(envId, "investigator");
    const actions = el("div", { class: "row" });
    const transitions = { open: ["acknowledged", "fixed"], acknowledged: ["open", "fixed"], accepted_risk: ["open"], fixed: ["verified", "open"], verified: ["open"] }[f.state] || [];
    if (can) for (const s of transitions) actions.append(el("button", { class: "btn sm", onclick: async () => { try { await api("PATCH", `/api/findings/${f.id}`, { state: s }); await refresh(); } catch (err) { toast(err.message, "error"); } } }, s === "verified" ? "Mark verified (cite evidence)" : `Mark ${s.replace("_", " ")}`));
    if (can && f.state !== "accepted_risk") actions.append(el("button", { class: "btn sm", onclick: () => acceptDialog(f) }, "Accept as intentional"));
    const exception = f.exception_id ? el("div", { class: "notice ok" }, "Accepted as intentional: exception ", el("a", { href: "#/knowledge" }, el("span", { class: "mono xs" }, f.exception_id))) : f.exception_note ? el("div", { class: "notice" }, f.exception_note) : null;
    return el("div", { class: "inspector stack-sm" },
      el("div", { class: "row" }, el("span", { class: `badge ${KIND_BADGE[f.kind] || ""}` }, f.kind), el("span", { class: `badge ${STATE_BADGE[f.state] || ""}` }, f.state.replace("_", " ")), el("span", { class: "mono xs muted" }, f.condition)),
      el("h2", {}, f.title),
      el("div", { class: "byline" }, `by ${f.author} · ${when(f.created_at)}${f.revisions.length ? ` · ${f.revisions.length} revision${f.revisions.length > 1 ? "s" : ""}` : ""}`),
      el("p", {}, f.explanation),
      exception,
      kv([["resources", f.resources.map(resourceText).join(", ")], ["period", f.period.rule || ""], ["uncertainty", f.uncertainty], ["missing", f.missing_information.length ? el("ul", { style: "margin:0;padding-left:16px" }, f.missing_information.map((m) => el("li", {}, m))) : ""], ["next steps", f.next_steps.length ? el("ul", { style: "margin:0;padding-left:16px" }, f.next_steps.map((m) => el("li", {}, m))) : ""], ["owner", f.owner]]),
      el("h3", {}, "Evidence for"), ...f.evidence_for.map(evLink),
      f.evidence_against.length ? el("h3", {}, "Evidence against") : null, ...f.evidence_against.map(evLink),
      f.knowledge_used.length ? el("div", { class: "notice" }, "Influenced by team knowledge: ", f.knowledge_used.map((k) => el("span", { class: "mono xs" }, k + " "))) : null,
      actions,
      can ? el("details", {}, el("summary", {}, "Edit (kept as a revision with your byline)"), form([{ name: "title", label: "Title", value: f.title }, { name: "explanation", label: "Explanation", type: "textarea", value: f.explanation }, { name: "owner", label: "Owner", value: f.owner || "" }], async (data) => { await api("PATCH", `/api/findings/${f.id}`, { edits: { title: data.title, explanation: data.explanation, owner: data.owner || null } }); await refresh(); }, "Save edit")) : null,
    );
  }

  function acceptDialog(f) {
    const dialog = el("div", { class: "inspector stack-sm" }, el("h2", {}, "Accept as intentional"), el("p", { class: "muted small" }, `This records that ${f.condition} on ${f.resources.map(resourceText).join(", ")} is intentional. It applies to exactly this condition and these resources in this environment, never to similar ones.`),
      form([
        { name: "explanation", label: "Why is this intentional?", type: "textarea", required: true },
        { name: "review_at", label: "Review on (optional)", type: "date" },
        { name: "expires_at", label: "Expires on (optional)", type: "date" },
        { name: "reconsider_when", label: "Reconsider when (optional)", placeholder: "e.g. the scheduler stops being a singleton" },
        { name: "trigger_replicas", label: "Reopen if the replica count changes", type: "checkbox", value: true },
      ], async (data) => {
        await api("POST", `/api/findings/${f.id}/accept`, { explanation: data.explanation, review_at: data.review_at || null, expires_at: data.expires_at || null, reconsider_when: data.reconsider_when || "", triggers: data.trigger_replicas ? ["replicas_change"] : [] });
        toast("Accepted. Later investigations will show this as accepted, not as a new problem.");
        selected = { kind: "finding", id: f.id };
        await refresh();
      }, "Accept"),
      el("button", { class: "btn quiet sm", onclick: () => { draw(); } }, "Cancel"));
    root.querySelector(".inspector").replaceWith(dialog);
  }

  function draw() {
    const body = { findings, activity, evidence, history }[tab]();
    root.replaceChildren(header(), answer(), nextAction(), el("div", { class: "grid-main" }, el("div", { class: "stack-sm" }, tabs(), body), inspector()));
  }

  async function refresh() { bundle = await api("GET", `/api/investigations/${id}`); draw(); }

  function listen() {
    if (stream) stream.close();
    if (!["running", "queued"].includes(inv().status)) return;
    stream = new EventSource(`/api/investigations/${id}/events`);
    let timer = null;
    stream.onmessage = () => { clearTimeout(timer); timer = setTimeout(() => refresh().then(() => { if (!["running", "queued"].includes(inv().status) && stream) { stream.close(); stream = null; } }), 150); };
    stream.onerror = () => { if (stream) { stream.close(); stream = null; setTimeout(() => { if (location.hash.endsWith(id)) refresh().then(listen); }, 2000); } };
  }

  draw();
  listen();
}
