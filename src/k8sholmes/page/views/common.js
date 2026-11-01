// Shared helpers: the API client, element building, formatting, the toast, shared state.
export const state = { me: undefined, needsSetup: false, environments: [], env: null };

export class ApiError extends Error {
  constructor(status, message, payload) { super(message); this.status = status; this.payload = payload; }
}

export async function api(method, path, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin" });
  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = { error: text }; }
  if (!response.ok) throw new ApiError(response.status, (payload && payload.error) || `HTTP ${response.status}`, payload);
  return payload;
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("aria-")) node.setAttribute(k, String(v));
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function toast(message, kind = "") {
  const host = document.getElementById("toast");
  const node = el("div", { class: `toast ${kind}` }, message);
  host.replaceChildren(node);
  setTimeout(() => { if (node.parentNode) node.remove(); }, 6000);
}

export function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

export function ms(n) { return n == null ? "" : n < 1000 ? `${n} ms` : `${(n / 1000).toFixed(1)} s`; }

export const STATUS_BADGE = { completed: "ok", partial: "review", running: "accent", queued: "", cancelled: "", failed: "severe", interrupted: "review" };
export function statusBadge(status) { return el("span", { class: `badge ${STATUS_BADGE[status] || ""}` }, status); }
export const COVERAGE_BADGE = { observed: "ok", observed_absent: "info", no_data: "review", cannot_answer: "review" };
export function coverageBadge(coverage) { return el("span", { class: `badge ${COVERAGE_BADGE[coverage] || ""}`, title: "coverage" }, coverage.replace("_", " ")); }
export const KIND_BADGE = { observation: "info", hypothesis: "review", conclusion: "ok" };
export const STATE_BADGE = { open: "", acknowledged: "info", accepted_risk: "", fixed: "review", verified: "ok" };

export function resourceText(r) { return `${r.kind}${r.namespace ? ` ${r.namespace}/` : " "}${r.name}`; }

export function emptyState(text, action) { return el("div", { class: "state" }, text, action ? el("div", { style: "margin-top:8px" }, action) : null); }

export function kv(pairs) {
  const dl = el("dl", { class: "kv" });
  for (const [k, v] of pairs) { if (v === null || v === undefined || v === "") continue; dl.append(el("dt", {}, k), el("dd", {}, v)); }
  return dl;
}

export function environmentPicker(onchange) {
  const select = el("select", { "aria-label": "Environment", onchange: (e) => { state.env = e.target.value; onchange && onchange(state.env); } });
  for (const env of state.environments) select.append(el("option", { value: env.id, selected: env.id === currentEnv() }, env.name));
  return select;
}

export function currentEnv() {
  if (state.env && state.environments.some((e) => e.id === state.env)) return state.env;
  state.env = state.environments.length ? state.environments[0].id : null;
  return state.env;
}

export function roleIn(envId) {
  if (!state.me) return null;
  if (state.me.is_admin) return "admin";
  const grant = (state.me.grants || []).find((g) => g.environment_id === envId);
  return grant ? grant.role : null;
}
export const ROLE_ORDER = { viewer: 0, investigator: 1, operator: 2, admin: 3 };
export function hasRole(envId, needed) { const r = roleIn(envId); return r !== null && ROLE_ORDER[r] >= ROLE_ORDER[needed]; }

function inputFor(field, id) {
  switch (field.type) {
    case "textarea":
      return el("textarea", { id, name: field.name, placeholder: field.placeholder || "", required: field.required }, field.value ?? "");
    case "select": {
      const select = el("select", { id, name: field.name });
      for (const [value, label] of field.options) select.append(el("option", { value, selected: value === field.value }, label));
      return select;
    }
    case "checkbox":
      return el("input", { id, name: field.name, type: "checkbox", checked: field.value });
    default:
      return el("input", { id, name: field.name, type: field.type || "text", placeholder: field.placeholder || "", required: field.required, value: field.value ?? "" });
  }
}

export function form(fields, onsubmit, submitLabel = "Save") {
  const f = el("form", { class: "stack-sm" });
  async function submit(e) {
    e.preventDefault();
    const button = f.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await onsubmit(Object.fromEntries(new FormData(f).entries()), f);
    } catch (err) {
      toast(err.message, "error");
    } finally {
      button.disabled = false;
    }
  }
  f.addEventListener("submit", submit);
  for (const field of fields) {
    const id = `f-${field.name}-${Math.random().toString(36).slice(2, 7)}`;
    f.append(el("div", { class: "field" },
      el("label", { for: id }, field.label),
      inputFor(field, id),
      field.help ? el("div", { class: "faint xs" }, field.help) : null,
    ));
  }
  f.append(el("div", { class: "row" }, el("button", { class: "btn primary", type: "submit" }, submitLabel)));
  return f;
}
