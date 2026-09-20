"""The runtime context herdr hands to a plugin command.

herdr injects HERDR_* environment variables into action, event, pane and startup commands (real samples
are kept in tests/fixtures/herdr_events/). This module is only about reading them: which pane and agent
the command concerns, which event fired, and where the plugin may keep its config and state. The config
itself lives in config.py and the watched panes in registry.py.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple

DEFAULT_PLUGIN_ID = "maaalo.herdr-markmap"


@dataclass(frozen=True)
class PluginEnv:
    plugin_id: str
    root: Optional[Path]
    config_dir: Path
    state_dir: Path
    bin_path: str
    pane_id: Optional[str]
    workspace_id: Optional[str]
    context: dict
    event: Optional[dict]
    event_name: Optional[str]
    event_status: Optional[str]
    action_id: Optional[str]
    entrypoint_id: Optional[str]
    agent_kind: Optional[str]
    agent_cwd: Optional[str]
    workspace_label: Optional[str]
    environ: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)


def _parse_json(raw: Optional[str]) -> dict:
    """A JSON object from an environment variable; anything unreadable is treated as absent."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _default_dirs(environ: Mapping[str, str], plugin_id: str) -> Tuple[Path, Path]:
    """Config and state directories to use when herdr does not inject its own (running outside a pane)."""
    home = Path(environ.get("HOME") or Path.home())
    state_home = Path(environ.get("XDG_STATE_HOME") or home / ".local" / "state")
    config_home = Path(environ.get("XDG_CONFIG_HOME") or home / ".config")
    return config_home / "herdr" / "plugins" / "config" / plugin_id, state_home / "herdr" / "plugins" / plugin_id


def load_plugin_env(environ: Optional[Mapping[str, str]] = None) -> PluginEnv:
    """Read the HERDR_* variables of the current command. Event data wins over the focused-pane context."""
    environ = os.environ if environ is None else environ
    plugin_id = environ.get("HERDR_PLUGIN_ID") or DEFAULT_PLUGIN_ID
    context = _parse_json(environ.get("HERDR_PLUGIN_CONTEXT_JSON"))
    event = _parse_json(environ.get("HERDR_PLUGIN_EVENT_JSON")) or None
    event_data = (event or {}).get("data") or {}
    default_config, default_state = _default_dirs(environ, plugin_id)

    return PluginEnv(
        plugin_id=plugin_id,
        root=Path(environ["HERDR_PLUGIN_ROOT"]) if environ.get("HERDR_PLUGIN_ROOT") else None,
        config_dir=Path(environ.get("HERDR_PLUGIN_CONFIG_DIR") or default_config),
        state_dir=Path(environ.get("HERDR_PLUGIN_STATE_DIR") or default_state),
        bin_path=environ.get("HERDR_BIN_PATH") or "herdr",
        pane_id=event_data.get("pane_id") or environ.get("HERDR_PANE_ID") or context.get("focused_pane_id"),
        workspace_id=event_data.get("workspace_id") or environ.get("HERDR_WORKSPACE_ID") or context.get("workspace_id"),
        context=context,
        event=event,
        event_name=environ.get("HERDR_PLUGIN_EVENT") if event else None,
        event_status=event_data.get("agent_status"),
        action_id=environ.get("HERDR_PLUGIN_ACTION_ID"),
        entrypoint_id=environ.get("HERDR_PLUGIN_ENTRYPOINT_ID"),
        agent_kind=event_data.get("agent") or context.get("focused_pane_agent"),
        agent_cwd=context.get("focused_pane_cwd") or context.get("workspace_cwd"),
        workspace_label=context.get("workspace_label"),
        environ=environ,
    )
