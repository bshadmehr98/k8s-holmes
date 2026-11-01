"""Investigations in the background and their live events (design §15.2, §16.2).

One thread per running investigation; the engine's events go to every subscriber of that investigation
(server-sent events). Idempotency keys stop a repeated click from starting two investigations.
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


class EventBus:
    """Per-investigation queues; `publish` is the engine's `on_event` hook."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[queue.Queue[Event]]] = {}

    def subscribe(self, investigation_id: str) -> queue.Queue[Event]:
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
        investigation_id = str(payload.get("investigation_id", ""))
        with self._lock:
            subscribers = list(self._subscribers.get(investigation_id, []))
        for q in subscribers:
            q.put((kind, payload))


class Runner:
    """Runs `engine.run` and `engine.follow_up` in threads; one at a time per investigation."""

    def __init__(
        self,
        engine: Engine,
        *,
        bus: EventBus | None = None,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        self.engine = engine
        self.bus = bus or EventBus()
        self.engine.on_event = self.bus.publish
        self._thread_factory = thread_factory
        self._lock = threading.Lock()
        self._running: dict[str, threading.Thread] = {}
        self._keys: dict[str, str] = {}

    def by_key(self, key: str) -> str | None:
        with self._lock:
            return self._keys.get(key)

    def is_running(self, investigation_id: str) -> bool:
        with self._lock:
            thread = self._running.get(investigation_id)
            return thread is not None and thread.is_alive()

    def _spawn(self, investigation_id: str, target: Callable[[], Investigation]) -> None:
        with self._lock:
            live = self._running.get(investigation_id)
            if live is not None and live.is_alive():
                return

            def work() -> None:
                try:
                    target()
                except Exception:
                    log.exception("investigation %s failed in the background", investigation_id)
                finally:
                    with self._lock:
                        self._running.pop(investigation_id, None)

            thread = self._thread_factory(target=work, name=f"investigation-{investigation_id}", daemon=True)
            self._running[investigation_id] = thread
            thread.start()

    def run(self, investigation_id: str, user: User, *, key: str | None = None) -> None:
        if key:
            with self._lock:
                self._keys[key] = investigation_id
        self._spawn(investigation_id, lambda: self.engine.run(investigation_id, user))

    def follow_up(self, investigation_id: str, user: User, message: str) -> None:
        if self.is_running(investigation_id):
            self.engine.follow_up(investigation_id, user, message)  # a redirect: appended to the thread
            return
        self._spawn(investigation_id, lambda: self.engine.follow_up(investigation_id, user, message))

    def wait(self, investigation_id: str, timeout: float | None = None) -> None:
        with self._lock:
            thread = self._running.get(investigation_id)
        if thread is not None:
            thread.join(timeout)
