"""The runner: one thread per investigation, events to subscribers, keys, follow-ups in the background."""

import queue
import threading
import time
from pathlib import Path

from k8sholmes.app import App
from k8sholmes.records import Investigation
from k8sholmes.runner import Runner


def test_events_reach_subscribers_of_the_investigation_only(tmp_path: Path) -> None:
    runner = Runner(App.open(tmp_path / "home", extensions=False).engine)
    a = runner.subscribe("inv_a")
    b = runner.subscribe("inv_b")
    runner.publish("step", {"investigation_id": "inv_a", "n": 1})
    runner.publish("step", {"investigation_id": "inv_c", "n": 2})
    assert a.get_nowait() == ("step", {"investigation_id": "inv_a", "n": 1})
    assert b.empty()
    runner.unsubscribe("inv_a", a)
    runner.unsubscribe("inv_a", a)  # twice is fine
    runner.publish("step", {"investigation_id": "inv_a", "n": 3})
    assert a.empty()


def test_runner_runs_once_per_investigation_and_follows_up_in_the_background(tmp_path: Path) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    env, user = app.setup_demo()
    runner = Runner(app.engine)
    inv = app.engine.start(environment=env, question="Why is api restarting?", user=user)
    q = runner.subscribe(inv.id)
    runner.run(inv.id, user, key="k")
    runner.run(inv.id, user, key="k")  # a second start while running is ignored
    assert runner.by_key("k") == inv.id and runner.by_key("other") is None
    runner.wait(inv.id, timeout=30)
    assert not runner.is_running(inv.id)
    kinds = []
    while True:
        try:
            kinds.append(q.get_nowait()[0])
        except queue.Empty:
            break
    assert "concluded" in kinds
    assert app.store.get(Investigation, inv.id).status == "completed"
    runner.follow_up(inv.id, user, "What changed before this started?")
    runner.wait(inv.id, timeout=30)
    assert app.store.get(Investigation, inv.id).status in ("completed", "partial")


def test_a_failing_background_run_is_logged_not_raised(tmp_path: Path, caplog) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    env, user = app.setup_demo()

    class Boom:
        name = "boom"

        def complete(self, *args, **kwargs):
            raise RuntimeError("no model today")

    app.engine.model_for = lambda inv: Boom()
    runner = Runner(app.engine)
    inv = app.engine.start(environment=env, question="x", user=user)
    runner.run(inv.id, user)
    runner.wait(inv.id, timeout=30)
    assert app.store.get(Investigation, inv.id).status == "failed"
    assert "failed in the background" in caplog.text
    assert threading.active_count() >= 1
    time.sleep(0)
