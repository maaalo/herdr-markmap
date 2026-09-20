"""Plugin configuration, read from HERDR_PLUGIN_CONFIG_DIR/config.json.

Every field has a default, so the plugin runs before the user has written anything. `write_defaults`
drops an editable copy of those defaults next to the state on first use; unknown or badly typed keys
in that file are ignored rather than fatal, since it is hand-edited.
"""
import json
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Optional, Tuple

from .backends import DEFAULT_MODEL
from .files import read_json, safe_name, write_json

TRIGGER_STATUSES_DEFAULT = ("idle", "done")


@dataclass(frozen=True)
class Config:
    output_dir: Optional[str] = None  # default: <agent cwd>/.mindmap
    model: str = DEFAULT_MODEL
    backend: str = "claude-cli"
    effort: Optional[str] = None  # reasoning effort for the initial build (backend default when unset)
    merge_effort: Optional[str] = "medium"  # reasoning effort for incremental merges; patches need little reasoning
    scratch_dir: Optional[str] = None
    max_retries: int = 1
    settle_seconds: float = 1.5  # seconds to wait after the status change for the history write to settle
    trigger_statuses: Tuple[str, ...] = TRIGGER_STATUSES_DEFAULT
    lock_timeout: float = 120.0
    preview: bool = True  # run the markmap live-preview server in the background
    preview_port_base: int = 8765  # first port tried; the next free one is used
    sidebar_token: str = "mindmap"  # token name rendered as $mindmap in the herdr sidebar
    sidebar_icon: str = "🗺"
    notify: bool = False
    instant_placeholder: bool = True  # show the pending turns in the map right away, before the model merges them

    @classmethod
    def write_defaults(cls, config_dir: Path) -> Path:
        """Write the editable default config file, only when it does not exist yet."""
        path = config_path(config_dir)
        if not path.exists():
            data = asdict(cls())
            data["trigger_statuses"] = list(cls().trigger_statuses)
            write_json(path, data)
        return path


def config_path(config_dir: Path) -> Path:
    return Path(config_dir) / "config.json"


def load_config(config_dir: Path) -> Config:
    """The stored config merged onto the defaults; a missing or damaged file gives plain defaults."""
    data = read_json(config_path(config_dir))
    if data is None:
        return Config()
    defaults = Config()
    overrides = {f.name: _coerce(f.type, data[f.name], getattr(defaults, f.name)) for f in fields(Config) if f.name in data}
    return replace(defaults, **overrides)


def _coerce(field_type, value, default):
    """Bring a JSON value to the type the Config field declares; strings and Optional[str] pass through."""
    if field_type is bool:
        return bool(value)
    if field_type is int:
        return int(value)
    if field_type is float:
        return float(value)
    if field_type == Tuple[str, ...]:
        return tuple(str(v) for v in value) if isinstance(value, (list, tuple)) else default
    return value


def default_output_path(config: Config, cwd: str, label: Optional[str], pane_id: str) -> Path:
    """Where the mind map of a pane is written: the configured directory, or <agent cwd>/.mindmap."""
    directory = Path(os.path.expanduser(config.output_dir)) if config.output_dir else Path(cwd) / ".mindmap"
    return directory / (safe_name(label or pane_id) + ".md")
