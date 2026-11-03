"""Background check jobs for the web interface (one at a time, kept in memory)."""
from __future__ import annotations

import copy
import sys
import threading
import traceback
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from ..checking import current_timestamp
from ..service import ConflictError, NotFoundError, ServiceError, Workspace

MAX_JOBS_KEPT = 20


class CheckJobs:
    """Runs ``Workspace.check`` in a thread and exposes progress and results."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._latest: str | None = None
        self._counter = 0

    def start(self, ids: Sequence[str] | None) -> dict[str, Any]:
        """Start a check of all enabled entries or of the given IDs."""
        if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
            raise ServiceError("'ids' must be a list of entry IDs")
        known = {entry["id"] for entry in self.workspace.entries()}
        for entry_id in ids or ():
            if entry_id not in known:
                raise NotFoundError(f"No entry with ID '{entry_id}'")
        with self._lock:
            latest = self._jobs.get(self._latest) if self._latest else None
            if latest is not None and latest["status"] == "running":
                raise ConflictError("A check is already running")
            self._counter += 1
            job = {
                "id": str(self._counter),
                "status": "running",
                "total": len(known) if ids is None else len(ids),
                "done": 0,
                "results": [],
                "error": None,
                "started_at": current_timestamp(),
                "finished_at": None,
            }
            self._jobs[job["id"]] = job
            self._latest = job["id"]
            for old in list(self._jobs)[:-MAX_JOBS_KEPT]:
                del self._jobs[old]
            snapshot = copy.deepcopy(job)
        threading.Thread(target=self._run, args=(job, ids), daemon=True).start()
        return snapshot

    def _run(self, job: dict[str, Any], ids: Sequence[str] | None) -> None:
        def started(total: int) -> None:
            with self._lock:
                job["total"] = total

        def progress(done: int, total: int, result: dict[str, Any]) -> None:
            with self._lock:
                job["done"] = done
                job["total"] = total
                job["results"].append(result)

        try:
            self.workspace.check(ids, progress=progress, started=started)
            outcome: tuple[str, str | None] = ("done", None)
        except (ServiceError, OSError, ValueError) as error:
            outcome = ("failed", str(error))
        except Exception:  # a bug: keep the details in the server log
            traceback.print_exc(file=sys.stderr)
            outcome = ("failed", "Internal error")
        with self._lock:
            job["status"], job["error"] = outcome
            job["finished_at"] = current_timestamp()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise NotFoundError(f"No check job '{job_id}'")
            return copy.deepcopy(job)

    def latest(self) -> dict[str, Any] | None:
        """The most recent job, so a reloaded page can resume showing it."""
        with self._lock:
            job = self._jobs.get(self._latest) if self._latest else None
            return copy.deepcopy(job) if job else None
