"""The composition root: the home folder, the store, auth, the registry, the engine and the models.

`App.open(home)` is what the command line and the web server use; tests build their own `App` with a
fake cluster and a scripted model. The demo environment (design §17.2) is created here too.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from k8sholmes.auth import Auth
from k8sholmes.capabilities import Registry
from k8sholmes.capabilities.builtin import builtin_registry
from k8sholmes.cluster import Cluster
from k8sholmes.engine import Engine
from k8sholmes.environments import cluster_for, connect, create_environment
from k8sholmes.errors import InvalidInput, NotFound
from k8sholmes.models import Model, OpenAICompatible, api_key_for, usable_for_investigations
from k8sholmes.records import Environment, Grant, Investigation, ModelEndpoint, User
from k8sholmes.store import Store
from k8sholmes.testing.cluster import FakeCluster
from k8sholmes.testing.demo import build as build_demo
from k8sholmes.testing.demo_model import DemoModel

DEMO_ENDPOINT_ID = "mdl_demo"
DEMO_ENVIRONMENT = "demo"


def default_home() -> Path:
    return Path(os.environ.get("K8SHOLMES_HOME") or Path.home() / ".k8sholmes")


@dataclass
class App:
    home: Path
    store: Store
    auth: Auth
    registry: Registry
    engine: Engine = field(init=False)
    clusters: dict[str, Cluster] = field(default_factory=dict[str, Cluster])  # in-memory clusters (tests)

    def __post_init__(self) -> None:
        self.engine = Engine(
            self.store, self.auth, self.registry, cluster_for=self.cluster_for, model_for=self.model_for
        )

    @classmethod
    def open(cls, home: Path | str | None = None, *, extensions: bool = True) -> App:
        home = Path(home) if home else default_home()
        home.mkdir(parents=True, exist_ok=True)
        store = Store(home / "k8sholmes.db")
        registry = builtin_registry(
            home=home, allowed_executables=_allowed_executables(home), extensions=extensions
        )
        return cls(home=home, store=store, auth=Auth(store), registry=registry)

    # environments and models

    def cluster_for(self, environment: Environment) -> Cluster:
        if environment.id in self.clusters:
            return self.clusters[environment.id]
        return cluster_for(environment)

    def model_for(self, investigation: Investigation) -> Model:
        endpoint_id = investigation.model_endpoint_id
        if endpoint_id == DEMO_ENDPOINT_ID:
            return DemoModel()
        if not endpoint_id:
            defaults = self.store.find(ModelEndpoint, {"is_default": True})
            if not defaults:
                raise InvalidInput(
                    "no model endpoint is configured; add one in Settings (or run `k8sholmes demo`)"
                )
            endpoint = defaults[0]
        else:
            endpoint = self.store.get(ModelEndpoint, endpoint_id)
        usable_for_investigations(endpoint)
        return OpenAICompatible(endpoint, api_key_for(endpoint, self.home))

    def environment(self, name_or_id: str) -> Environment:
        try:
            return self.store.get(Environment, name_or_id)
        except NotFound:
            found = self.store.find(Environment, {"name": name_or_id})
            if not found:
                raise NotFound(
                    f"no environment named {name_or_id!r}; `k8sholmes environments` lists them"
                ) from None
            return found[0]

    def endpoint(self, name_or_id: str) -> ModelEndpoint:
        try:
            return self.store.get(ModelEndpoint, name_or_id)
        except NotFound:
            found = self.store.find(ModelEndpoint, {"name": name_or_id})
            if not found:
                raise NotFound(f"no model endpoint named {name_or_id!r}") from None
            return found[0]

    # the demo

    def setup_demo(self, *, after_fix: bool = False) -> tuple[Environment, User]:
        """The demo environment with its recording, the demo model endpoint and a demo admin (§17.2)."""
        recording = self.home / "recordings" / DEMO_ENVIRONMENT
        build_demo(after_fix=after_fix).save(recording)
        existing = self.store.find(Environment, {"name": DEMO_ENVIRONMENT})
        if existing:
            env = existing[0].model_copy(update={"recording": str(recording)})
            self.store.put(env)
        else:
            env = create_environment(
                self.store, name=DEMO_ENVIRONMENT, context="demo", recording=str(recording)
            )
        env = env.model_copy(
            update={"settings": env.settings.model_copy(update={"default_model_id": DEMO_ENDPOINT_ID})}
        )
        self.store.put(env)
        env = connect(self.store, env)
        try:
            self.store.get(ModelEndpoint, DEMO_ENDPOINT_ID)
        except NotFound:
            self.store.put(
                ModelEndpoint(
                    id=DEMO_ENDPOINT_ID,
                    name="demo investigator (rule-based, offline)",
                    base_url="demo://",
                    model="demo-investigator",
                    is_default=not self.store.find(ModelEndpoint, {"is_default": True}),
                )
            )
        users = self.store.find(User, {"username": "demo"})
        user = users[0] if users else self.auth.create_user("demo", "demo-password", is_admin=True)
        if not any(g.environment_id == env.id for g in user.grants):
            user = self.auth.set_grant(user, env.id, "operator")
        return env, user

    def demo_cluster(self, *, after_fix: bool = False) -> FakeCluster:
        return build_demo(after_fix=after_fix)


def _allowed_executables(home: Path) -> list[str]:
    path = home / "allowed-executables.txt"
    if not path.exists():
        return []
    return [
        line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")
    ]


def grant_role(app: App, username: str, environment: Environment, role: str) -> User:
    users = app.store.find(User, {"username": username})
    if not users:
        raise NotFound(f"no user named {username!r}")
    return app.auth.set_grant(users[0], environment.id, role)  # type: ignore[arg-type]


__all__ = ["DEMO_ENDPOINT_ID", "App", "Callable", "Grant", "grant_role"]
