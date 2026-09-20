"""Reading the HERDR_* variables herdr injects into a plugin command."""
import json
from pathlib import Path

from markmap_pipeline.plugin_context import PluginEnv, load_plugin_env

FIXTURES = Path(__file__).parent / "fixtures" / "herdr_events"


def environ_from_fixture(name, **overrides):
    fx = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    env = dict(fx["env"])
    env["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps(fx["context"])
    if fx["event"] is not None:
        env["HERDR_PLUGIN_EVENT_JSON"] = json.dumps(fx["event"])
    env.update(overrides)
    return env


class TestLoadPluginEnv:
    def test_action_env_exposes_pane_agent_cwd_and_label(self):
        env = load_plugin_env(environ_from_fixture("action-working.json", HERDR_PLUGIN_ACTION_ID="start"))
        assert env.plugin_id == "maaalo.herdr-markmap-spike"
        assert env.pane_id == "w5:p1"
        assert env.agent_kind == "claude"
        assert env.agent_cwd == "/Users/x/git/herdr-markmap"
        assert env.workspace_label == "herdr-markmap"
        assert env.action_id == "start"
        assert env.event_name is None and env.event_status is None
        assert env.state_dir == Path("/Users/x/.local/state/herdr/plugins/maaalo.herdr-markmap-spike")
        assert env.config_dir == Path("/Users/x/.config/herdr/plugins/config/maaalo.herdr-markmap-spike")
        assert env.bin_path == "/opt/homebrew/bin/herdr"

    def test_event_env_uses_event_pane_and_status(self):
        env = load_plugin_env(environ_from_fixture("agent_status_changed-done.json"))
        assert env.event_name == "pane.agent_status_changed"
        assert env.event_status == "done"
        assert env.pane_id == "w5:p4"
        assert env.agent_kind == "claude"
        assert env.agent_cwd == "/tmp/spike-agent"

    def test_event_pane_id_comes_from_event_data_even_without_env_var(self):
        environ = environ_from_fixture("agent_status_changed-idle.json")
        environ.pop("HERDR_PANE_ID")
        env = load_plugin_env(environ)
        assert env.pane_id == "w5:p4"
        assert env.event_status == "idle"

    def test_missing_or_invalid_json_is_tolerated(self):
        env = load_plugin_env({"HERDR_PLUGIN_ID": "x", "HERDR_PLUGIN_CONTEXT_JSON": "{not json", "HERDR_PANE_ID": "w1:p1"})
        assert env.pane_id == "w1:p1"
        assert env.agent_kind is None
        assert env.context == {}

    def test_dirs_fall_back_to_xdg_defaults_when_not_injected(self, monkeypatch):
        monkeypatch.setenv("HOME", "/home/u")
        env = load_plugin_env({"HERDR_PLUGIN_ID": "maaalo.herdr-markmap"})
        assert env.state_dir == Path("/home/u/.local/state/herdr/plugins/maaalo.herdr-markmap")
        assert env.config_dir == Path("/home/u/.config/herdr/plugins/config/maaalo.herdr-markmap")
