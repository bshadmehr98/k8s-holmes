"""The acceptance tests run the product as a user would: `App` on a temporary home, the demo environment."""

from __future__ import annotations

from pathlib import Path

import pytest

from k8sholmes.app import App
from k8sholmes.records import Environment, User


class Demo:
    def __init__(self, tmp_path: Path, *, after_fix: bool = False) -> None:
        self.app = App.open(tmp_path / "home", extensions=False)
        self.env, self.user = self.app.setup_demo(after_fix=after_fix)

    def ask(self, question: str, *, user: User | None = None, env: Environment | None = None):
        inv = self.app.engine.start(environment=env or self.env, question=question, user=user or self.user)
        return self.app.engine.run(inv.id, user or self.user)


@pytest.fixture
def demo(tmp_path: Path) -> Demo:
    return Demo(tmp_path)
