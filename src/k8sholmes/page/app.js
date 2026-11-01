// K8sHolmes interface: a hash router over a JSON API (design §15). No build step, no external code.
import { renderSignIn, renderSetup } from "./views/account.js";
import { renderHome } from "./views/home.js";
import { renderInvestigations } from "./views/investigations.js";
import { renderInvestigation } from "./views/investigation.js";
import { renderCapabilities } from "./views/capabilities.js";
import { renderKnowledge } from "./views/knowledge.js";
import { renderSettings } from "./views/settings.js";
import { api, el, state, toast } from "./views/common.js";

const ROUTES = [
  { path: /^\/$/, view: renderHome, nav: "Investigate" },
  { path: /^\/investigations$/, view: renderInvestigations, nav: "Investigations" },
  { path: /^\/investigations\/([a-z0-9_]+)$/, view: renderInvestigation, nav: "Investigations" },
  { path: /^\/capabilities$/, view: renderCapabilities, nav: "Capabilities" },
  { path: /^\/knowledge$/, view: renderKnowledge, nav: "Knowledge" },
  { path: /^\/settings(?:\/([a-z]+))?$/, view: renderSettings, nav: "Settings" },
];
const NAV = [["Investigate", "#/"], ["Investigations", "#/investigations"], ["Capabilities", "#/capabilities"], ["Knowledge", "#/knowledge"]];

function renderNav(current) {
  const nav = document.getElementById("nav");
  nav.replaceChildren(...NAV.map(([label, href]) => {
    const a = el("a", { href }, label);
    if (label === current) a.setAttribute("aria-current", "page");
    return a;
  }));
  const account = document.getElementById("account");
  if (state.me) {
    account.replaceChildren(
      el("a", { href: "#/settings", class: "muted" }, "Settings"),
      el("span", { class: "mono xs" }, state.me.username),
      el("button", { class: "btn quiet sm", onclick: signOut }, "Sign out"),
    );
  } else {
    account.replaceChildren();
  }
}

async function signOut() {
  await api("DELETE", "/api/session");
  state.me = null;
  location.hash = "#/";
  route();
}

export async function route() {
  const main = document.getElementById("main");
  const hash = location.hash.replace(/^#/, "") || "/";
  main.replaceChildren(el("div", { class: "state" }, "Loading…"));
  try {
    if (state.me === undefined) {
      const me = await api("GET", "/api/me");
      state.me = me.user;
      state.needsSetup = me.needs_setup;
      state.environments = me.environments || [];
    }
    if (state.needsSetup) { renderNav(""); return renderSetup(main); }
    if (!state.me) { renderNav(""); return renderSignIn(main, route); }
    for (const r of ROUTES) {
      const m = hash.match(r.path);
      if (m) {
        renderNav(r.nav);
        document.title = `${r.nav} · K8sHolmes`;
        return await r.view(main, ...m.slice(1));
      }
    }
    main.replaceChildren(el("div", { class: "state" }, "There is no such page.", el("div", {}, el("a", { href: "#/" }, "Back to Investigate"))));
  } catch (err) {
    if (err.status === 401) { state.me = null; renderNav(""); return renderSignIn(main, route); }
    main.replaceChildren(el("div", { class: "state error" }, el("strong", {}, "Something went wrong"), el("div", { class: "muted small" }, String(err.message || err))));
    toast(String(err.message || err), "error");
  }
}

window.addEventListener("hashchange", route);
route();
