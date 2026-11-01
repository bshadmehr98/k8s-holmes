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


def test_the_finished_hook_sees_the_run_and_its_kind_and_a_failing_hook_leaves_the_run_failed(
    tmp_path: Path, caplog
) -> None:
    app = App.open(tmp_path / "home", extensions=False)
    env, user = app.setup_demo()
    seen: list[tuple[str, str, str]] = []
    runner = Runner(app.engine, on_finished=lambda inv, kind: seen.append((inv.id, inv.status, kind)))
    inv = app.engine.start(environment=env, question="Why is api restarting?", user=user)
    runner.run(inv.id, user)
    runner.wait(inv.id, timeout=30)
    assert seen == [(inv.id, "completed", "run")]
    runner.follow_up(inv.id, user, "and the worker?")
    runner.wait(inv.id, timeout=30)
    assert seen[-1][0] == inv.id and seen[-1][2] == "follow_up"

    def boom(finished: Investigation, kind: str) -> None:
        raise RuntimeError("the hook broke")

    runner.on_finished = boom
    second = app.engine.start(environment=env, question="Why is api restarting?", user=user)
    runner.run(second.id, user)
    runner.wait(second.id, timeout=30)
    assert app.store.get(Investigation, second.id).status == "completed"  # the run itself was fine
    assert "the hook broke" in caplog.text  # the hook's failure is logged, never hidden
