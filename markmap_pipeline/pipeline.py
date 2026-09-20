"""One merge cycle, plus the processed-position state and the per-output lock it relies on.

Triggering is the job of the herdr event hook (plugin.py). This module takes the pending turns from a
source, hands them to the updater, and advances the processed position only on success.
"""
import datetime
import fcntl
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from .backends import BackendError
from .files import read_json, write_json
from .sources import SourceState
from .updater import UpdateResult

log = logging.getLogger("markmap_pipeline")


# ---------------------------------------------------------------------------
# Processed position
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineState:
    source: SourceState = field(default_factory=SourceState)
    updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {"source": self.source.to_dict(), "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, data: dict) -> "PipelineState":
        return cls(source=SourceState.from_dict(data.get("source") or {}), updated_at=data.get("updated_at"))


def load_state(path: Union[str, Path]) -> PipelineState:
    """The stored position; a missing or damaged file starts the source from the beginning."""
    data = read_json(path)
    return PipelineState() if data is None else PipelineState.from_dict(data)


def save_state(path: Union[str, Path], state: PipelineState) -> None:
    write_json(path, state.to_dict())


# ---------------------------------------------------------------------------
# One merge cycle
# ---------------------------------------------------------------------------


class Pipeline:
    def __init__(self, source, updater, state_path: Union[str, Path]):
        self.source = source
        self.updater = updater
        self.state_path = Path(state_path)

    def run_update(self) -> UpdateResult:
        """Poll the source, merge the pending turns and advance the position; concurrent runs are kept apart by acquire_lock."""
        state = load_state(self.state_path)
        turns, source_state = self.source.poll(state.source)
        if not turns:
            save_state(self.state_path, PipelineState(source=source_state, updated_at=state.updated_at))
            return UpdateResult(status="noop")
        log.info("Merging %d turn(s) from %s", len(turns), source_state.path)
        try:
            result = self.updater.update(turns)
        except BackendError as exc:
            # Keep the offset so the same turns are sent again on the next trigger
            log.error("Backend error; will retry on the next change: %s", exc)
            return UpdateResult(status="error", message=str(exc))
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save_state(self.state_path, PipelineState(source=source_state, updated_at=now))
        log.info("%s (%d attempt(s))%s", result.status, result.attempts, (" " + result.message) if result.message else "")
        return result


# ---------------------------------------------------------------------------
# Per-output exclusive lock
# ---------------------------------------------------------------------------


class LockError(Exception):
    pass


class FileLock:
    def __init__(self, path: Path):
        self.path = path
        self._fd: Optional[int] = None

    def acquire(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise LockError("Another update is already running for this output (lock: %s)" % self.path)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def acquire_lock(output_path: Union[str, Path], timeout: float = 0.0, poll: float = 0.2) -> FileLock:
    """With timeout > 0, wait for the lock to be released (serializes back-to-back event hooks)."""
    output_path = Path(output_path)
    lock = FileLock(output_path.parent / (".%s.lock" % output_path.name))
    deadline = time.monotonic() + timeout
    while True:
        try:
            return lock.acquire()
        except LockError:
            if time.monotonic() >= deadline:
                if timeout > 0:
                    raise LockError("Timed out after %ss waiting for lock %s (another update is still running)" % (timeout, lock.path))
                raise
            time.sleep(poll)
