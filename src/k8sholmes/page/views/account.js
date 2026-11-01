import { api, el, form, state } from "./common.js";

export function renderSignIn(main, onDone) {
  main.replaceChildren(
    el("div", { class: "stack", style: "max-width:380px;margin:48px auto" },
      el("h1", {}, "Sign in"),
      el("p", { class: "muted" }, "Your account was created by an administrator of this K8sHolmes."),
      form([
        { name: "username", label: "Username", required: true },
        { name: "password", label: "Password", type: "password", required: true },
      ], async (data) => {
        await api("POST", "/api/session", data);
        state.me = undefined;
        onDone();
      }, "Sign in"),
    ),
  );
}

export function renderSetup(main) {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  main.replaceChildren(
    el("div", { class: "stack", style: "max-width:420px;margin:48px auto" },
      el("h1", {}, "Set up K8sHolmes"),
      el("p", { class: "muted" }, "This K8sHolmes has no accounts yet. The one-time setup token was printed when the server started (and is in the home folder as setup-token). Create the first administrator with it."),
      form([
        { name: "token", label: "Setup token", required: true, value: params.get("token") || "" },
        { name: "username", label: "Administrator username", required: true },
        { name: "password", label: "Password (8 characters or more)", type: "password", required: true },
      ], async (data) => {
        await api("POST", "/api/setup", data);
        await api("POST", "/api/session", { username: data.username, password: data.password });
        state.me = undefined; state.needsSetup = false;
        location.hash = "#/settings/environments";
        location.reload();
      }, "Create the administrator"),
    ),
  );
}
