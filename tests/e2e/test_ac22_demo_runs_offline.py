"""Guarantee 22: the demo and its scenarios produce their expected findings and limitations offline."""

from k8sholmes.records import Finding
from tests.e2e.conftest import Demo


def conditions(demo: Demo, inv) -> dict[str, Finding]:
    return {f.condition: f for f in demo.app.store.find(Finding, {"investigation_id": inv.id})}


def test_scenario_1_why_is_api_restarting(demo: Demo) -> None:
    inv = demo.ask("Why is api restarting?")
    assert inv.status == "completed", inv.limitations
    found = conditions(demo, inv)
    assert "pod.crashloop.oom" in found
    assert found["pod.crashloop.oom"].kind == "observation"
    assert "256Mi" in found["pod.crashloop.oom"].explanation
    assert "change.before-incident" in found
    assert found["change.before-incident"].kind == "hypothesis"
    assert inv.answer is not None and "256Mi" in inv.answer
    assert inv.limitations == []  # everything it claims rests on observed evidence


def test_scenario_2_unused_services_are_not_called_unused(demo: Demo) -> None:
    inv = demo.ask("Which services appear unused or obsolete?")
    found = conditions(demo, inv)
    assert "service.no-backends" in found
    assert found["service.no-backends"].resources[0].name == "legacy-export"
    assert "cannot" in found["service.no-backends"].explanation
    assert any("cannot_answer" in lim for lim in inv.limitations)
    assert inv.status == "partial"  # a stated limitation keeps it from "completed"


def test_scenario_3_isolation_says_what_it_cannot_know(demo: Demo) -> None:
    inv = demo.ask("Is this environment isolated from external networks?")
    found = conditions(demo, inv)
    assert "exposure.loadbalancer" in found
    assert "policy.unselected-pods" in found
    assert inv.answer is not None and "cannot be answered" in inv.answer
    assert any("outside the cluster" in lim for lim in inv.limitations)


def test_scenario_4_missing_alerts_then_an_accepted_exception(demo: Demo) -> None:
    from k8sholmes import knowledge, records
    from k8sholmes.records import ResourceRef

    inv = demo.ask("Which important alerts are missing?")
    found = demo.app.store.find(Finding, {"investigation_id": inv.id})
    without_alerts = {f.resources[0].name for f in found if f.condition == "alert.missing"}
    assert {"api", "worker", "scheduler"} <= without_alerts
    assert "web" not in without_alerts
    single = [f for f in found if f.condition == "replicas.single" and f.resources[0].name == "scheduler"]
    assert single and single[0].state == "open"
    assert any(
        "fired" in lim and "cannot_answer" in lim for lim in inv.limitations
    )  # whether alerts fired is unknown
    knowledge.create_exception(
        demo.app.store,
        records.AcceptedException(
            environment_id=demo.env.id,
            condition="replicas.single",
            scope=ResourceRef(kind="Deployment", namespace="shop", name="scheduler"),
            explanation="singleton scheduler; downtime is acceptable",
            approved_by=f"user:{demo.user.id}",
        ),
    )
    again = demo.ask("Which important alerts are missing?")
    found_again = demo.app.store.find(Finding, {"investigation_id": again.id})
    scheduler = next(
        f for f in found_again if f.condition == "replicas.single" and f.resources[0].name == "scheduler"
    )
    assert scheduler.state == "accepted_risk"
    assert scheduler.exception_id is not None
    worker = next(
        f for f in found_again if f.condition == "replicas.single" and f.resources[0].name == "worker"
    )
    assert worker.state == "open"


def test_scenario_5_what_changed_before(demo: Demo) -> None:
    inv = demo.ask("What changed before this problem started?")
    found = conditions(demo, inv)
    assert "change.before-incident" in found
    assert "256Mi" in found["change.before-incident"].explanation
    assert inv.limitations == []


def test_the_fix_is_visible_in_the_after_state(tmp_path) -> None:
    demo = Demo(tmp_path, after_fix=True)
    inv = demo.ask("Did the latest change resolve the api issue?")
    found = conditions(demo, inv)
    assert "custom.healthy-after-change" in found
    assert "pod.crashloop.oom" not in found
    assert inv.answer is not None and "verif" in inv.answer
