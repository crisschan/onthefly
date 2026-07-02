"""Workspace paths, file locking, and runtime initialization."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time
from typing import Iterator

from .errors import ToolError


LOCK_POLL_SECONDS = 0.05
LOCK_TIMEOUT_SECONDS = 5.0


def workspace_root() -> Path:
    return Path.cwd()


def otf_root() -> Path:
    return workspace_root() / "otf_tools"


def otf_state_dir() -> Path:
    return otf_root() / ".otf"


@contextmanager
def file_lock(lock_path: Path, timeout_seconds: float | None = None) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if timeout_seconds is None:
        timeout_seconds = _configured_lock_timeout()
    deadline = time.monotonic() + timeout_seconds

    with lock_path.open("a", encoding="utf-8") as lock_file:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise ToolError("lock_timeout", f"Timed out acquiring lock: {lock_path}") from exc
                time.sleep(LOCK_POLL_SECONDS)

        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def ensure_otf_tools() -> None:
    state_dir = otf_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    with file_lock(state_dir / ".init.lock"):
        state_dir.mkdir(parents=True, exist_ok=True)


def _configured_lock_timeout() -> float:
    raw = os.getenv("OTF_LOCK_TIMEOUT_SECONDS")
    if raw is None:
        return LOCK_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return LOCK_TIMEOUT_SECONDS
    return value if value > 0 else LOCK_TIMEOUT_SECONDS
