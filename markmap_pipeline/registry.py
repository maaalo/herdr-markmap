"""Registry of the panes being watched, one JSON file per pane under the state directory.

herdr gives each plugin a state directory (HERDR_PLUGIN_STATE_DIR); a pane's entry records where its
mind map lives and what its preview server is doing. Separate files keep concurrent panes from
overwriting each other, and the derived paths (processed position, preview log) are named from the
same pane id so everything about a pane can be found without a central index.
"""
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import List, Optional

from .files import read_json, safe_name, write_json


@dataclass(frozen=True)
class WatchedAgent:
    pane_id: str
    cwd: str
    agent_kind: str
    label: Optional[str]
    output: str
    workspace_id: Optional[str] = None
    preview_pid: Optional[int] = None
    preview_port: Optional[int] = None
    preview_url: Optional[str] = None
    registered_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WatchedAgent":
        return cls(**{f.name: data.get(f.name) for f in fields(cls)})


class Registry:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.agents_dir = self.state_dir / "agents"

    def _path(self, pane_id: str) -> Path:
        return self.agents_dir / (safe_name(pane_id) + ".json")

    def state_path(self, pane_id: str) -> Path:
        """Where the processed position (PipelineState) of the merge is stored."""
        return self.state_dir / "positions" / (safe_name(pane_id) + ".state.json")

    def preview_log_path(self, pane_id: str) -> Path:
        """Where the background markmap process writes its stdout (which contains the URL)."""
        return self.state_dir / "previews" / (safe_name(pane_id) + ".log")

    def register(self, agent: WatchedAgent) -> None:
        write_json(self._path(agent.pane_id), agent.to_dict())

    def update(self, pane_id: str, **changes) -> Optional[WatchedAgent]:
        """Store a changed copy of an entry; None when the pane is no longer registered."""
        current = self.get(pane_id)
        if current is None:
            return None
        updated = replace(current, **changes)
        self.register(updated)
        return updated

    def get(self, pane_id: str) -> Optional[WatchedAgent]:
        return self._load(self._path(pane_id))

    def unregister(self, pane_id: str) -> bool:
        path = self._path(pane_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def all(self) -> List[WatchedAgent]:
        if not self.agents_dir.is_dir():
            return []
        agents = [self._load(p) for p in self.agents_dir.glob("*.json")]
        return sorted((a for a in agents if a is not None), key=lambda a: a.pane_id)

    @staticmethod
    def _load(path: Path) -> Optional[WatchedAgent]:
        """An entry, or None when the file is missing, damaged or has no pane id."""
        data = read_json(path)
        if data is None or not data.get("pane_id"):
            return None
        return WatchedAgent.from_dict(data)
