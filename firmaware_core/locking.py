"""Advisory file lock so several firmAware processes can share state and configuration."""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:  # not available on Windows; locking is then skipped
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

LOCK_TIMEOUT_SECONDS = 30.0
LOCK_POLL_SECONDS = 0.05


@contextmanager
def file_lock(path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Hold an exclusive lock on ``<path>.lock`` while the block runs.

    The lock is per open file, so it also excludes threads of the same process.
    Keep the protected block short: read, change, write.
    """
    if fcntl is None:  # pragma: no cover
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Could not lock {path.name}: another firmAware process is using it"
                    ) from None
                time.sleep(LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
