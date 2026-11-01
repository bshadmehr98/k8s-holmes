"""The store keeps records, finds them by indexed columns, and refuses a newer schema (design §16.1)."""

import sqlite3
import threading
from pathlib import Path

import pytest

from k8sholmes import records
from k8sholmes.errors import NotFound, StoreVersionError
from k8sholmes.store import SCHEMA_VERSION, TABLES, Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "k8sholmes.db")


def test_put_get_and_roundtrip(store: Store) -> None:
    env = records.Environment(name="dev", context="kind-dev")
    store.put(env)
    assert store.get(records.Environment, env.id) == env
    env2 = env.model_copy(update={"name": "dev2"})
    store.put(env2)
    assert store.get(records.Environment, env.id).name == "dev2"
    assert store.count(records.Environment) == 1


def test_find_by_indexed_columns_and_null(store: Store) -> None:
    inv = records.Investigation(environment_id="env_1", question="why?", created_by="usr_1")
    store.put(inv)
    store.put(
        records.Investigation(environment_id="env_2", question="what?", created_by="usr_1", saved_id="sav_1")
    )
    assert [i.id for i in store.find(records.Investigation, {"environment_id": "env_1"})] == [inv.id]
    assert len(store.find(records.Investigation, {"saved_id": None})) == 1
    assert store.find(records.Investigation, {"status": "running"}) == []


def test_missing_record_is_not_found(store: Store) -> None:
    with pytest.raises(NotFound, match="Environment 'env_x' does not exist"):
        store.get(records.Environment, "env_x")


def test_delete(store: Store) -> None:
    k = records.Knowledge(environment_id="e", kind="ownership", text="team a", author="user:u")
    store.put(k)
    store.delete(records.Knowledge, k.id)
    assert store.count(records.Knowledge) == 0


def test_every_table_has_a_record_class_with_an_id() -> None:
    for table, (cls, columns) in TABLES.items():
        assert "id" in cls.model_fields, table
        for column in columns:
            assert column in cls.model_fields, (table, column)


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "k8sholmes.db"
    Store(path).close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    conn.commit()
    conn.close()
    with pytest.raises(StoreVersionError, match="newer K8sHolmes"):
        Store(path)


def test_reopen_keeps_data_and_version(tmp_path: Path) -> None:
    path = tmp_path / "k8sholmes.db"
    s = Store(path)
    s.put(records.Environment(name="a", context="c"))
    s.close()
    again = Store(path)
    assert again.schema_version == SCHEMA_VERSION
    assert again.count(records.Environment) == 1


def test_transaction_rolls_back_on_error(store: Store) -> None:
    def write_then_fail() -> None:
        with store.transaction():
            store.put(records.Environment(name="a", context="c"))
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        write_then_fail()
    assert store.count(records.Environment) == 0


def test_threads_get_their_own_connections(store: Store) -> None:
    errors: list[BaseException] = []

    def work(n: int) -> None:
        try:
            for i in range(20):
                store.put(
                    records.Knowledge(environment_id="e", kind="lesson", text=f"{n}-{i}", author="user:u")
                )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert store.count(records.Knowledge) == 80


def test_a_version_1_store_gains_the_milestone_3_tables_and_the_new_records_round_trip(
    tmp_path: Path,
) -> None:
    path = tmp_path / "k8sholmes.db"
    first = Store(path)
    first.put(records.Environment(name="a", context="c"))
    first.close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_version SET version = 1")
    for table in ("reports", "shares", "proposals", "saved_investigations"):
        conn.execute(f"DROP TABLE {table}")
    conn.commit()
    conn.close()
    s = Store(path)
    assert s.schema_version == SCHEMA_VERSION == 2
    assert s.count(records.Environment) == 1  # version 1 data survives the migration
    report = records.Report(
        environment_id="env_a",
        kind="investigation",
        title="t",
        subject_id="inv_a",
        generated_by="user:u",
        sections=[{"heading": "h", "kind": "list", "body": ["x"], "counted": {}}],
        numbers={"findings": 1},
        edits=[records.ReportEdit(author="user:u", section="h", text="note")],
    )
    share = records.Share(
        report_id=report.id,
        environment_id="env_a",
        token_hash="h" * 64,
        created_by="user:u",
        expires_at=records.now(),
    )
    proposal = records.Proposal(
        environment_id="env_a",
        investigation_id="inv_a",
        finding_ids=["fnd_a"],
        kind="task",
        title="t",
        change="c",
        intended_effect="e",
        verification_condition="x",
        author="user:u",
        verifications=[records.Verification(by="user:u", outcome="not_verified", condition="x")],
    )
    saved = records.SavedInvestigation(
        environment_id="env_a",
        question="q",
        created_by="u",
        schedule=records.Schedule(every_hours=2, next_run_at=records.now()),
        notification=records.Notification(webhook_url_env="HOOK", frequency="daily_digest"),
    )
    for record in (report, share, proposal, saved):
        s.put(record)
    s.close()
    again = Store(path)
    assert again.find(records.Share, {"token_hash": "h" * 64})[0].id == share.id
    assert (
        again.find(records.Report, {"kind": "investigation", "subject_id": "inv_a"}, order_by="revision")[0]
        .edits[0]
        .text
        == "note"
    )
    assert again.find(records.Proposal, {"state": "proposed"})[0].verifications[0].outcome == "not_verified"
    assert again.get(records.SavedInvestigation, saved.id).notification.frequency == "daily_digest"
    assert again.count(records.Share, {"expires_at": share.expires_at}) == 1
