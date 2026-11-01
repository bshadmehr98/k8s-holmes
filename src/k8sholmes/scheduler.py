"""The server's background thread (design §12, §16.3): starts saved investigations when they are due and
runs the retention purge once a day. Schedules run only while the server runs (§20)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from k8sholmes.auth import Auth
from k8sholmes.records import Environment, SavedInvestigation, now
from k8sholmes.retention import purge
from k8sholmes.saved import next_run
from k8sholmes.store import Store

log = logging.getLogger("k8sholmes")


class Scheduler:
    """The server's background thread: due saved investigations and the nightly purge (design §12, §16.3)."""

    def __init__(
        self,
        store: Store,
        *,
        run_saved: Callable[[SavedInvestigation], None],
        auth: Auth,
        interval_s: float = 60.0,
        clock: Callable[[], datetime] = now,
    ) -> None:
        self.store = store
        self.run_saved = run_saved
        self.auth = auth
        self.interval_s = interval_s
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_purge: datetime | None = None

    def tick(self) -> list[str]:
        """One pass: start what is due; purge once a day. Returns the saved ids started."""
        started: list[str] = []
        at = self.clock()
        for saved in self.store.find(SavedInvestigation):
            due = (
                saved.schedule.enabled
                and saved.schedule.next_run_at is not None
                and saved.schedule.next_run_at <= at
            )
            if not due:
                continue
            moved = saved.schedule.model_copy(update={"next_run_at": next_run(saved, at)})
            self.store.put(saved.model_copy(update={"schedule": moved}))  # a failing start never loops
            try:
                self.run_saved(saved)
                started.append(saved.id)
            except Exception:
                log.exception("scheduled run of %s failed to start", saved.id)
        if self.last_purge is None or self.last_purge < at - timedelta(hours=24):
            for env in self.store.find(Environment):
                purge(self.store, self.auth, env)
            self.last_purge = at
        return started

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(self.interval_s):
                try:
                    self.tick()
                except Exception:
                    log.exception("scheduler tick failed")

        self._thread = threading.Thread(target=loop, name="k8sholmes-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)


__all__ = ["Scheduler"]
