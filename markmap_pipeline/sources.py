"""Conversation log sources.

ClaudeJsonlSource follows, read-only, the history Claude Code appends to
~/.claude/projects/<slug>/<sessionId>.jsonl. The newest session file is resolved on every poll, so a
/clear or a new session is followed automatically.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import herdr
from .extract import Turn, parse_record, read_new_turns

DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


@dataclass(frozen=True)
class SourceState:
    """Processed position. offset resets to 0 when path changes; cursor holds the previous snapshot tail for polling sources."""

    path: Optional[str] = None
    offset: int = 0
    cursor: Optional[str] = None

    def to_dict(self) -> dict:
        data = {"path": self.path, "offset": self.offset}
        if self.cursor is not None:
            data["cursor"] = self.cursor
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SourceState":
        return cls(path=data.get("path"), offset=int(data.get("offset", 0) or 0), cursor=data.get("cursor"))


def project_slug(cwd: str) -> str:
    """The slug Claude Code uses for a project directory: `/` and whitespace become `-`.

    The exact rule is undocumented, so find_session_file falls back to scanning every directory by cwd.
    """
    return re.sub(r"[/\s]", "-", cwd)


def project_dir_candidates(cwd: str, projects_root: Path) -> Tuple[List[Path], bool]:
    """Project directories to search and whether they are the exact slug match (else every directory is scanned by cwd)."""
    projects_root = Path(projects_root)
    if not projects_root.is_dir():
        return [], False
    slug_dir = projects_root / project_slug(cwd)
    if slug_dir.is_dir():
        return [slug_dir], True
    return [p for p in projects_root.iterdir() if p.is_dir()], False


def jsonl_cwd(path: Path) -> Optional[str]:
    """Return the first cwd found in the JSONL (to check which directory a session belongs to)."""
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                record = parse_record(line)
                if record and isinstance(record.get("cwd"), str):
                    return record["cwd"]
    except OSError:
        return None
    return None


def _session_files(directory: Path) -> List[Path]:
    return [p for p in directory.iterdir() if p.is_file() and p.suffix == ".jsonl"]


def find_session_file(cwd: str, projects_root: Path = DEFAULT_PROJECTS_ROOT, session_id: Optional[str] = None) -> Optional[Path]:
    """Return the newest session JSONL for cwd; an explicit session_id takes precedence."""
    candidates, exact = project_dir_candidates(cwd, Path(projects_root))
    if session_id:
        for directory in candidates:
            path = directory / (session_id + ".jsonl")
            if path.is_file():
                return path
    files = [p for directory in candidates for p in _session_files(directory) if exact or jsonl_cwd(p) == cwd]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


class ClaudeJsonlSource:
    def __init__(self, cwd: str, projects_root: Path = DEFAULT_PROJECTS_ROOT, session_id: Optional[str] = None):
        self.cwd = cwd
        self.projects_root = Path(projects_root)
        self.session_id = session_id

    def resolve(self) -> Optional[Path]:
        return find_session_file(self.cwd, self.projects_root, self.session_id)

    def poll(self, state: SourceState) -> Tuple[List[Turn], SourceState]:
        """Return the pending turns and the updated state; a switched session file is read from the start."""
        path = self.resolve()
        if path is None:
            return [], state
        offset = state.offset if state.path == str(path) else 0
        turns, new_offset = read_new_turns(path, offset)
        return turns, SourceState(path=str(path), offset=new_offset)


# ---------------------------------------------------------------------------
# herdr read fallback (for agents other than Claude Code)
# ---------------------------------------------------------------------------

_CURSOR_TAIL_LINES = 40


def new_lines_since(previous_tail: Optional[List[str]], current: List[str]) -> List[str]:
    """Compare the previous snapshot tail with the current snapshot and return only the new lines.

    Terminal snapshots scroll away at the top, so find where the previous tail appears in the current
    snapshot and treat everything after it as new. With no match, all lines are new. Blank lines are ignored.
    """
    current_nonblank = [line for line in current if line.strip()]
    if not previous_tail:
        return current_nonblank
    prev = [line for line in previous_tail if line.strip()]
    if not prev:
        return current_nonblank
    # Prefer the longest match: do the last k lines appear contiguously somewhere in current?
    for k in range(min(len(prev), len(current_nonblank)), 0, -1):
        needle = prev[-k:]
        for start in range(len(current_nonblank) - k, -1, -1):
            if current_nonblank[start : start + k] == needle:
                return current_nonblank[start + k :]
    return current_nonblank


class HerdrReadSource:
    """Poll `herdr agent read` and return the lines added since last time as one transcript turn.

    Being a terminal snapshot, it cannot tell user from assistant and misses the alternate screen.
    Use ClaudeJsonlSource for Claude Code agents.
    """

    def __init__(self, target: str, lines: int = 400, reader: Optional[Callable[[str, int], str]] = None):
        self.target = target
        self.lines = lines
        self.reader = reader or (lambda t, n: herdr.agent_read(t, lines=n))

    def poll(self, state: SourceState) -> Tuple[List[Turn], SourceState]:
        key = "herdr:%s" % self.target
        text = self.reader(self.target, self.lines)
        current = text.splitlines()
        previous_tail = None
        if state.path == key and state.cursor:
            try:
                previous_tail = json.loads(state.cursor)
            except ValueError:
                previous_tail = None
        fresh = new_lines_since(previous_tail, current)
        tail = [line for line in current if line.strip()][-_CURSOR_TAIL_LINES:]
        new_state = SourceState(path=key, offset=0, cursor=json.dumps(tail, ensure_ascii=False))
        if not fresh:
            return [], new_state
        return [Turn(role="transcript", text="\n".join(fresh))], new_state
