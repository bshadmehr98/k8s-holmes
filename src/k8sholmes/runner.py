"""Investigations in the background and their live events (design §15.2, §16.2).

One thread per running investigation; the engine's events go to every subscriber of that investigation
(the server-sent event stream reads a subscriber's queue). Idempotency keys stop a repeated click from
starting two investigations. Whatever fails in the background leaves the investigation `failed`, never
`running` (§16.2).
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

from k8sholmes.engine import Engine
from k8sholmes.records import Investigation, User

log = logging.getLogger("k8sholmes")
Event = tuple[str, dict[str, Any]]
Finished = Callable[[Investigation, str], None]  # the finished investigation and "run" or "follow_up"


class Runner:
    """Runs `engine.run` and `engine.follow_up` in threads, one at a time per investigation, and fans the
    engine's events out to subscribers."""

    def __init__(self, engine: Engine, *, on_finished: Finished | None = None) -> None:
        self.engine = engine
        self.on_finished = on_finished  # called in the run's thread after it ends: (investigation, kind)
        self.engine.on_event = self.publish
        self._lock = threading.Lock()
        self._running: dict[str, threading.Thread] = {}
        self._keys: dict[str, str] = {}
        self._subscribers: dict[str, list[queue.Queue[Event]]] = {}

    # events

    def subscribe(self, investigation_id: str) -> queue.Queue[Event]:
        """A queue that receives every event of one investigation from now on."""
        q: queue.Queue[Event] = queue.Queue()
        with self._lock:
            self._subscribers.setdefault(investigation_id, []).append(q)
        return q

    def unsubscribe(self, investigation_id: str, q: queue.Queue[Event]) -> None:
        with self._lock:
            subscribers = self._subscribers.get(investigation_id, [])
            if q in subscribers:
                subscribers.remove(q)

    def publish(self, kind: str, payload: dict[str, Any]) -> None:
        """The engine's `on_event` hook."""
        investigation_id = str(payload.get("investigation_id", ""))
        with self._lock:
            subscribers = list(self._subscribers.get(investigation_id, []))
        for q in subscribers:
            q.put((kind, payload))

    # runs

    def by_key(self, key: str) -> str | None:
        """The investigation an idempotency key started, while it is remembered."""
        with self._lock:
            return self._keys.get(key)

    def is_running(self, investigation_id: str) -> bool:
        with self._lock:
            thread = self._running.get(investigation_id)
            return thread is not None and thread.is_alive()

    def run(self, investigation_id: str, user: User, *, key: str | None = None) -> None:
        """Run an investigation in the background; a second call while it runs does nothing."""
        if key:
            with self._lock:
                self._keys[key] = investigation_id
        self._spawn(investigation_id, lambda: self.engine.run(investigation_id, user), key, "run")

    def follow_up(self, investigation_id: str, user: User, message: str) -> None:
        """A follow-up in the background; while the investigation runs, the message is a redirect."""
        if self.is_running(investigation_id):
            self.engine.follow_up(investigation_id, user, message)
            return
        self._spawn(
            investigation_id,
            lambda: self.engine.follow_up(investigation_id, user, message),
            None,
            "follow_up",
        )

    def wait(self, investigation_id: str, timeout: float | None = None) -> None:
        """Block until the investigation's thread ends (tests and the command line)."""
        with self._lock:
            thread = self._running.get(investigation_id)
        if thread is not None:
            thread.join(timeout)

    def _spawn(
        self, investigation_id: str, target: Callable[[], Investigation], key: str | None, kind: str
    ) -> None:
        with self._lock:
            live = self._running.get(investigation_id)
            if live is not None and live.is_alive():
                return

            def work() -> None:
                try:
                    finished = target()
                    if self.on_finished is not None:
                        self.on_finished(finished, kind)
                except Exception as exc:
                    log.exception("investigation %s failed in the background", investigation_id)
                    self._leave_failed(investigation_id, f"{type(exc).__name__}: {exc}")
                finally:
                    with self._lock:
                        self._running.pop(investigation_id, None)
                        if key:
                            self._keys.pop(key, None)

            thread = threading.Thread(target=work, name=f"investigation-{investigation_id}", daemon=True)
            self._running[investigation_id] = thread
            thread.start()

    def _leave_failed(self, investigation_id: str, reason: str) -> None:
        """A row never stays `running` without a thread behind it (design §16.2)."""
        inv = self.engine.store.get(Investigation, investigation_id)
        if inv.status in ("running", "queued"):
            self.engine.store.put(
                inv.model_copy(update={"status": "failed", "limitations": [*inv.limitations, reason]})
            )
            self.publish("failed", {"investigation_id": investigation_id, "error": reason})
