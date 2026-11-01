"""Persistence (design §16.1): one SQLite file, a versioned schema, one connection per thread.

Every table holds records of one kind: `id`, a few indexed columns copied from the record for queries,
and `data`, the record as JSON. Writes go through `put`, which validates the record on the way in; reads
validate on the way out, so a row a newer version wrote fails loudly instead of quietly.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from k8sholmes import records
from k8sholmes.errors import NotFound, StoreVersionError
from k8sholmes.records import Record

R = TypeVar("R", bound=Record)

SCHEMA_VERSION = 1

# table -> (record class, indexed columns copied from the record)
TABLES: dict[str, tuple[type[Record], tuple[str, ...]]] = {
    "environments": (records.Environment, ("name",)),
    "inventories": (records.Inventory, ("environment_id", "taken_at")),
    "users": (records.User, ("username",)),
    "sessions": (records.Session, ("token_hash", "user_id", "expires_at")),
    "audit": (records.AuditEntry, ("at", "user_id", "environment_id", "action")),
    "investigations": (
        records.Investigation,
        ("environment_id", "status", "created_at", "created_by", "saved_id"),
    ),
    "messages": (records.Message, ("investigation_id", "seq")),
    "steps": (records.Step, ("investigation_id", "seq", "phase", "status")),
    "calls": (records.Call, ("investigation_id", "step_id", "capability_id", "at", "status")),
    "evidence": (
        records.Evidence,
        ("investigation_id", "environment_id", "step_id", "source", "collected_at"),
    ),
    "findings": (records.Finding, ("investigation_id", "environment_id", "condition", "state", "created_at")),
    "exceptions": (records.AcceptedException, ("environment_id", "condition", "revoked_at", "expires_at")),
    "knowledge": (records.Knowledge, ("environment_id", "kind", "created_at")),
    "model_endpoints": (records.ModelEndpoint, ("name", "is_default")),
    "model_calls": (records.ModelCall, ("investigation_id", "endpoint_id", "at")),
}


def _ddl(table: str, columns: Sequence[str]) -> list[str]:
    cols = ", ".join(f"{c} TEXT" for c in columns)
    sep = ", " if cols else ""
    statements = [f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, {cols}{sep}data TEXT NOT NULL)"]
    statements.extend(f"CREATE INDEX IF NOT EXISTS ix_{table}_{c} ON {table} ({c})" for c in columns)
    return statements


def _column_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


class Store:
    """The store. `Store(path)` opens or creates the file and migrates it to this version."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._local = threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    # connections

    def _connection(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Several writes that commit together (a step with its calls and evidence, design §16.2)."""
        conn = self._connection()
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def close(self) -> None:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # schema

    def _migrate(self) -> None:
        conn = self._connection()
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        version = int(row[0]) if row else 0
        if version > SCHEMA_VERSION:
            raise StoreVersionError(
                f"{self.path} was written by a newer K8sHolmes (schema {version}; this version knows "
                f"{SCHEMA_VERSION}). Upgrade K8sHolmes to open it."
            )
        with self.transaction() as tx:
            for table, (_, columns) in TABLES.items():
                for statement in _ddl(table, columns):
                    tx.execute(statement)
            if version == 0:
                tx.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            elif version < SCHEMA_VERSION:
                tx.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))

    @property
    def schema_version(self) -> int:
        row = self._connection().execute("SELECT version FROM schema_version").fetchone()
        return int(row[0])

    # records

    @staticmethod
    def _table_for(record: Record) -> tuple[str, tuple[str, ...]]:
        for table, (cls, columns) in TABLES.items():
            if type(record) is cls:
                return table, columns
        raise TypeError(f"no table holds {type(record).__name__}")

    def put(self, record: Record) -> None:
        """Insert or replace one record (validated by its model)."""
        table, columns = self._table_for(record)
        payload = record.model_dump(mode="json")
        values = [getattr(record, c) for c in columns]
        placeholders = ", ".join("?" for _ in range(len(columns) + 2))
        names = ", ".join(["id", *columns, "data"])
        self._connection().execute(
            f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({placeholders})",
            (payload["id"], *(_column_value(v) for v in values), json.dumps(payload)),
        )

    def get(self, cls: type[R], record_id: str) -> R:
        table = self._table_name(cls)
        row = self._connection().execute(f"SELECT data FROM {table} WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise NotFound(f"{cls.__name__} {record_id!r} does not exist")
        return cls.model_validate_json(row[0])

    def find(
        self,
        cls: type[R],
        where: dict[str, Any] | None = None,
        *,
        order_by: str = "rowid",
        limit: int | None = None,
    ) -> list[R]:
        """Records whose indexed columns equal `where` (None matches NULL)."""
        table = self._table_name(cls)
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (where or {}).items():
            if value is None:
                clauses.append(f"{column} IS NULL")
            else:
                clauses.append(f"{column} = ?")
                params.append(_column_value(value))
        sql = f"SELECT data FROM {table}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {order_by}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self._connection().execute(sql, params).fetchall()
        return [cls.model_validate_json(r[0]) for r in rows]

    def delete(self, cls: type[Record], record_id: str) -> None:
        table = self._table_name(cls)
        self._connection().execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))

    def count(self, cls: type[Record], where: dict[str, Any] | None = None) -> int:
        return len(self.find(cls, where))

    @staticmethod
    def _table_name(record_class: type[Record]) -> str:
        for table, (klass, _) in TABLES.items():
            if klass is record_class:
                return table
        raise TypeError(f"no table holds {record_class.__name__}")
