import { api, el, hasRole, form, toast, when, statusBadge, kv } from "./common.js";

const FREQUENCIES = [["changes_only", "only when something changed"], ["every_run", "after every run"], ["daily_digest", "at most once a day"]];

export function savedSection(envId, data, reload) {
  const can = hasRole(envId, "investigator");
  const list = data.saved.length ? el("div", { class: "list" }, data.saved.map((s) => el("div", { class: "list-item" },
    el("div", { class: "stack-sm" },
      el("a", { class: "title", href: `#/saved/${s.id}` }, s.question),
      el("div", { class: "meta" }, `${s.runs.length} run${s.runs.length === 1 ? "" : "s"} · ${scheduleText(s.schedule)} · ${s.notification.webhook_url_env ? `notifies ${FREQUENCIES.find(([k]) => k === s.notification.frequency)[1]}` : "no notification"}${s.scope.namespaces.length ? ` · ${s.scope.namespaces.join(", ")}` : ""}`),
    ),
    can ? el("button", { class: "btn sm", onclick: async () => { try { const made = await api("POST", `/api/saved/${s.id}/run`); location.hash = `#/investigations/${made.investigation.id}`; } catch (err) { toast(err.message, "error"); } } }, "Run now") : null,
  ))) : el("div", { class: "muted small" }, "Nothing saved. Save an investigation from its page to run it again by hand or on a schedule; every run is compared with the one before.");
  return el("div", { class: "section" }, el("h2", {}, `Saved and recurring (${data.saved.length})`), list);
}

function scheduleText(s) {
  if (!s.enabled) return "schedule off";
  if (s.every_hours) return `every ${s.every_hours} h${s.next_run_at ? `, next ${when(s.next_run_at).replace(" ago", "")}` : ""}`;
  if (s.daily_at) return `daily at ${s.daily_at} UTC`;
  return "by hand";
}

export async function renderSaved(main, id) {
  let data = await api("GET", `/api/saved/${id}`);
  const root = el("div", { class: "stack" });
  main.replaceChildren(root);
  const can = () => hasRole(data.saved.environment_id, "investigator");
  async function reload() { data = await api("GET", `/api/saved/${id}`); draw(); }

  function draw() {
    const s = data.saved;
    const runs = data.runs.map((run, i) => [run, data.comparisons[i]]).reverse();
    root.replaceChildren(
      el("div", { class: "row between" }, el("div", {}, el("h1", {}, s.question), el("div", { class: "muted small" }, `saved ${when(s.created_at)} · ${scheduleText(s.schedule)} · period: ${s.period_rule}`)), can() ? el("button", { class: "btn primary", onclick: async () => { try { const made = await api("POST", `/api/saved/${id}/run`); location.hash = `#/investigations/${made.investigation.id}`; } catch (err) { toast(err.message, "error"); } } }, "Run now") : null),
      el("div", { class: "grid-2" },
        el("div", { class: "stack-sm" }, el("h3", {}, "Runs, newest first"), runs.length ? el("div", { class: "list" }, runs.map(([run, cmp]) => el("div", { class: "list-item" },
          el("div", { class: "stack-sm" }, el("a", { href: `#/investigations/${run.id}` }, `${when(run.created_at)} · ${run.model_name || "no model"}`), el("div", { class: "small" }, cmp ? cmp.summary : ""), cmp && cmp.before_id && (cmp.new.length || cmp.resolved.length || cmp.evidence_changed.length) ? el("div", { class: "byline" }, `${cmp.new.length} new · ${cmp.resolved.length} resolved · ${cmp.evidence_changed.length} with changed evidence · ${cmp.unchanged} unchanged`) : null),
          el("div", {}, statusBadge(run.status)),
        ))) : el("div", { class: "muted small" }, "No runs yet."),
        data.reports.length ? el("div", {}, el("h3", {}, "Comparison reports"), el("ul", { class: "small" }, data.reports.map((r) => el("li", {}, el("a", { href: `#/reports/${r.id}` }, `revision ${r.revision} · ${when(r.generated_at)}`))))) : null),
        el("div", { class: "stack-sm" },
          el("h3", {}, "Schedule"),
          can() ? form([
            { name: "mode", label: "Run", type: "select", value: s.schedule.every_hours ? "hours" : s.schedule.daily_at ? "daily" : "manual", options: [["manual", "by hand only"], ["hours", "every N hours"], ["daily", "daily at a time (UTC)"]] },
            { name: "every_hours", label: "Every N hours (1 or more)", type: "number", value: s.schedule.every_hours || "" },
            { name: "daily_at", label: "Daily at (HH:MM UTC)", value: s.schedule.daily_at || "" },
            { name: "enabled", label: "Schedule enabled", type: "checkbox", value: s.schedule.enabled },
          ], async (d) => { await api("PATCH", `/api/saved/${id}`, { schedule: { every_hours: d.mode === "hours" ? Number(d.every_hours) : null, daily_at: d.mode === "daily" ? d.daily_at : null, enabled: d.enabled === "on" } }); toast("Schedule saved."); await reload(); }) : kv([["schedule", scheduleText(s.schedule)]]),
          el("h3", {}, "Notification"),
          el("p", { class: "muted small" }, "The webhook URL is read from an environment variable of the server, never stored. The payload is the summary and a link, never evidence."),
          can() ? form([
            { name: "webhook_url_env", label: "Environment variable holding the webhook URL", value: s.notification.webhook_url_env || "", placeholder: "K8SHOLMES_HOOK_SHOP" },
            { name: "frequency", label: "Send", type: "select", value: s.notification.frequency, options: FREQUENCIES },
          ], async (d) => { await api("PATCH", `/api/saved/${id}`, { notification: { webhook_url_env: d.webhook_url_env || null, frequency: d.frequency } }); toast("Notification saved."); await reload(); }) : kv([["frequency", s.notification.frequency], ["variable", s.notification.webhook_url_env || "none"]]),
        ),
      ),
    );
  }
  draw();
}

