import { api, el, emptyState, environmentPicker, currentEnv, statusBadge, when } from "./common.js";

export async function renderInvestigations(main) {
  const envId = currentEnv();
  if (!envId) { main.replaceChildren(emptyState("No environment yet.")); return; }
  const data = await api("GET", `/api/investigations?env=${encodeURIComponent(envId)}`);
  const list = el("div", { class: "list" });
  for (const inv of data.investigations) {
    list.append(el("div", { class: "list-item link", tabindex: 0, role: "link", onclick: () => { location.hash = `#/investigations/${inv.id}`; }, onkeydown: (e) => { if (e.key === "Enter") location.hash = `#/investigations/${inv.id}`; } },
      el("div", {}, el("div", { class: "title" }, inv.question), el("div", { class: "meta" }, `${when(inv.created_at)} · ${inv.findings} findings · ${inv.model_name || "no model yet"}`)),
      el("div", {}, el("span", { class: `dot ${inv.status}` }), statusBadge(inv.status)),
    ));
  }
  main.replaceChildren(el("div", { class: "stack" },
    el("div", { class: "row between" }, el("h1", {}, "Investigations"), el("div", { class: "row small muted" }, "Environment", environmentPicker(() => renderInvestigations(main)))),
    data.investigations.length ? list : emptyState("Nothing has been investigated in this environment yet.", el("a", { href: "#/" }, "Ask a question")),
  ));
}
