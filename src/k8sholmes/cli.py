"""The command line (design §15.5). Exit codes: 0 success, 1 failure with a message, 2 usage."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from k8sholmes import __version__, server
from k8sholmes.app import App, default_home
from k8sholmes.auth import setup_token
from k8sholmes.errors import K8sHolmesError
from k8sholmes.records import Environment, Evidence, Investigation, Scope, User
from k8sholmes.reports import investigation_json, report_text


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="k8sholmes", description="K8sHolmes: investigate a Kubernetes environment from a question."
    )
    parser.add_argument("--version", action="version", version=f"k8sholmes {__version__}")
    parser.add_argument(
        "--home", type=Path, default=None, help="the home folder (default $K8SHOLMES_HOME or ~/.k8sholmes)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("investigate", help="ask a question about an environment and print the report")
    inv.add_argument("question")
    inv.add_argument("--env", required=True, help="the environment's name")
    inv.add_argument(
        "--namespace", "-n", action="append", default=[], help="a namespace in scope (repeatable)"
    )
    inv.add_argument(
        "--model",
        default=None,
        help="the model endpoint's name (default: the environment's or the default endpoint)",
    )
    inv.add_argument("--user", default=None, help="act as this user (default: the only admin, or `demo`)")
    inv.add_argument("--json", action="store_true", help="print the investigation as JSON")

    caps = sub.add_parser("capabilities", help="the catalog, with availability in an environment")
    caps.add_argument("--env", default=None)
    caps.add_argument("--json", action="store_true")

    sub.add_parser("environments", help="the environments")

    demo = sub.add_parser("demo", help="set up the demo environment and its offline investigator")
    demo.add_argument(
        "--after-fix", action="store_true", help="the state after the api's memory limit was raised"
    )
    demo.add_argument(
        "--question", default=None, help="run one investigation right away and print its report"
    )

    serve = sub.add_parser("serve", help="the web interface")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8800)
    serve.add_argument(
        "--allow-remote", action="store_true", help="bind a non-loopback host (put TLS in front)"
    )

    sub.add_parser("setup", help="print the one-time link that creates the first administrator")

    export = sub.add_parser("export", help="write an investigation's report, JSON and evidence to a folder")
    export.add_argument("investigation_id")
    export.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except K8sHolmesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    app = App.open(args.home or default_home())
    return COMMANDS[args.command](app, args)


def _environments(app: App, args: argparse.Namespace) -> int:
    for env in app.store.find(Environment, order_by="name"):
        kind = "recording" if env.recording else f"context {env.context}"
        connected = (
            "connected " + env.connected_at.isoformat(timespec="minutes")
            if env.connected_at
            else "never connected"
        )
        print(f"{env.name}\t{kind}\t{connected}")
    return 0


def _acting_user(app: App, username: str | None) -> User:
    if username:
        users = app.store.find(User, {"username": username})
        if not users:
            raise K8sHolmesError(f"no user named {username!r}")
        return users[0]
    admins = [u for u in app.store.find(User) if u.is_admin]
    if len(admins) == 1:
        return admins[0]
    demo = app.store.find(User, {"username": "demo"})
    if demo:
        return demo[0]
    raise K8sHolmesError("say who you are with --user (or run `k8sholmes demo` first)")


def _capabilities(app: App, args: argparse.Namespace) -> int:
    env = app.environment(args.env) if args.env else None
    user = _acting_user(app, None) if env else None
    rows: list[dict[str, Any]] = []
    for cap in app.registry.all():
        row = cap.describe()
        if env and user:
            refusal = app.registry.refusal(cap, user, env, app.auth)
            row["available"] = refusal is None
            row["why_not"] = refusal.reason if refusal else None
        rows.append(row)
    if args.json:
        print(json.dumps({"capabilities": rows, "broken_extensions": app.registry.broken}, indent=1))
        return 0
    for row in rows:
        mark = (
            ""
            if "available" not in row
            else ("  available" if row["available"] else f"  unavailable: {row['why_not']}")
        )
        print(f"{row['id']:<24} {row['class']:<8} {row['title']}{mark}")
    for origin, error in app.registry.broken.items():
        print(f"broken extension {origin}: {error}", file=sys.stderr)
    return 0


def _investigate(app: App, args: argparse.Namespace) -> int:
    env = app.environment(args.env)
    user = _acting_user(app, args.user)
    endpoint_id = app.endpoint(args.model).id if args.model else None
    inv = app.engine.start(
        environment=env,
        question=args.question,
        user=user,
        scope=Scope(namespaces=args.namespace),
        model_endpoint_id=endpoint_id,
    )
    if not args.json:
        app.engine.on_event = _print_event
    inv = app.engine.run(inv.id, user)
    if args.json:
        print(json.dumps(investigation_json(app.store, inv), indent=1, default=str))
    else:
        print()
        print(report_text(app.store, inv))
    return 0 if inv.status in ("completed", "partial") else 1


def _print_event(kind: str, payload: dict[str, Any]) -> None:
    if kind == "step" and payload["step"].get("status") == "completed":
        step = payload["step"]
        print(f"  [{step['phase']}] {step['summary']}", file=sys.stderr)
    elif kind == "call":
        call = payload["call"]
        print(
            f"    {call['capability_id']} -> {call['status']}"
            + (f" ({call['reason'][:80]})" if call["reason"] else ""),
            file=sys.stderr,
        )


def _demo(app: App, args: argparse.Namespace) -> int:
    env, user = app.setup_demo(after_fix=args.after_fix)
    print(f"demo environment ready in {app.home} (environment {env.name!r}, user demo / demo-password)")
    if args.question:
        inv = app.engine.start(
            environment=env, question=args.question, user=user, scope=Scope(namespaces=["shop"])
        )
        app.engine.on_event = _print_event
        inv = app.engine.run(inv.id, user)
        print()
        print(report_text(app.store, inv))
        return 0 if inv.status in ("completed", "partial") else 1
    print('try: k8sholmes investigate "Why is api restarting?" --env demo')
    return 0


def _export(app: App, args: argparse.Namespace) -> int:
    inv = app.store.get(Investigation, args.investigation_id)
    out: Path = args.out
    (out / "evidence").mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report_text(app.store, inv))
    payload = {"format_version": "1", **investigation_json(app.store, inv)}
    (out / "report.json").write_text(json.dumps(payload, indent=1, default=str))
    for ev in app.store.find(Evidence, {"investigation_id": inv.id}):
        (out / "evidence" / f"{ev.id}.json").write_text(
            json.dumps(ev.model_dump(mode="json"), indent=1, default=str)
        )
    print(f"exported {inv.id} to {out}")
    return 0


def _serve(app: App, args: argparse.Namespace) -> int:
    made = server.make_server(app, host=args.host, port=args.port, allow_remote=args.allow_remote)
    print(f"K8sHolmes is at {made.url}", file=sys.stderr)
    if made.api.setup_token:
        print(
            f"first start: create the administrator at {made.url}#/?token={made.api.setup_token}",
            file=sys.stderr,
        )
    try:
        made.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        made.shutdown()
        made.server_close()
    return 0


def _setup(app: App, args: argparse.Namespace) -> int:
    if not app.auth.needs_setup():
        print("this K8sHolmes is set up already; an administrator adds users in Settings", file=sys.stderr)
        return 1
    token = setup_token(app.home)
    print(f"start the server, then open http://127.0.0.1:8800/#/?token={token}")
    print(
        f"(the token is also in {app.home / 'setup-token'}; the server prints this link on its first start)"
    )
    return 0


COMMANDS = {
    "demo": _demo,
    "environments": _environments,
    "capabilities": _capabilities,
    "investigate": _investigate,
    "export": _export,
    "serve": _serve,
    "setup": _setup,
}


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
