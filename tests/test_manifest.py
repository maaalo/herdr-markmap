"""herdr-plugin.toml follows herdr's manifest rules and matches this package's subcommands."""
import re
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - Python 3.9/3.10
    import tomli as tomllib

from markmap_pipeline.plugin import COMMANDS

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = tomllib.loads((ROOT / "herdr-plugin.toml").read_text(encoding="utf-8"))

PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9.:_-]+$")
LOCAL_ID_RE = re.compile(r"^[A-Za-z0-9:_-]+$")  # dots are not allowed
KNOWN_EVENTS = {
    "pane.created", "pane.updated", "pane.closed", "pane.focused", "pane.moved", "pane.exited",
    "pane.agent_detected", "pane.output_matched", "pane.agent_status_changed", "pane.scroll_changed",
    "workspace.created", "workspace.updated", "workspace.renamed", "workspace.moved", "workspace.reordered",
    "workspace.closed", "workspace.focused", "tab.created", "tab.closed", "tab.focused", "tab.renamed", "tab.moved",
    "worktree.created", "worktree.opened", "worktree.removed", "layout.updated",
}
EVENTS_KNOWN_TO_0_8_2 = KNOWN_EVENTS - {"pane.updated"}  # observed: pane.updated is unknown to 0.8.2


def all_commands():
    for section in ("build", "startup", "actions", "events", "panes"):
        for item in MANIFEST.get(section, []):
            yield section, item


class TestMetadata:
    def test_required_fields(self):
        for key in ("id", "name", "version", "min_herdr_version"):
            assert MANIFEST.get(key), key
        assert PLUGIN_ID_RE.match(MANIFEST["id"])
        assert re.match(r"^\d+\.\d+\.\d+$", MANIFEST["version"])
        assert re.match(r"^\d+\.\d+\.\d+$", MANIFEST["min_herdr_version"])

    def test_platforms_exclude_windows_because_of_fcntl(self):
        assert set(MANIFEST["platforms"]) == {"linux", "macos"}

    def test_min_version_matches_verified_herdr(self):
        # only features verified on 0.8.2 (no popup placement etc.)
        assert MANIFEST["min_herdr_version"] == "0.8.2"


class TestEntrypoints:
    def test_local_ids_are_unique_and_valid(self):
        for section in ("actions", "panes"):
            ids = [item["id"] for item in MANIFEST.get(section, [])]
            assert len(ids) == len(set(ids)), section
            assert all(LOCAL_ID_RE.match(i) for i in ids), ids

    def test_expected_actions_events_panes_and_startup(self):
        assert {a["id"] for a in MANIFEST["actions"]} == {"start", "stop", "toggle", "status", "open-preview"}
        assert [e["on"] for e in MANIFEST["events"]] == ["pane.agent_status_changed"]
        assert "panes" not in MANIFEST  # the preview is a background markmap plus a sidebar token, not a pane
        assert len(MANIFEST["startup"]) == 1

    def test_pane_actions_require_pane_context(self):
        by_id = {a["id"]: a for a in MANIFEST["actions"]}
        assert by_id["start"]["contexts"] == ["pane"]
        assert by_id["stop"]["contexts"] == ["pane"]
        assert by_id["open-preview"]["contexts"] == ["pane"]
        assert by_id["toggle"]["contexts"] == ["pane"]

    def test_event_names_are_known_to_the_verified_version(self):
        for event in MANIFEST["events"]:
            assert event["on"] in EVENTS_KNOWN_TO_0_8_2

    def test_action_event_and_startup_commands_use_the_venv_relative_to_the_plugin_root(self):
        # actions/events/startup run with the plugin root as cwd, so the relative venv path works
        for section, item in all_commands():
            command = item["command"]
            assert isinstance(command, list) and all(isinstance(c, str) for c in command), (section, command)
            if section in ("build", "panes"):
                continue
            assert command[0] == ".venv/bin/python", (section, command)
            assert command[1:3] == ["-m", "markmap_pipeline.plugin"], (section, command)
            assert command[3] in COMMANDS, (section, command)

    def test_runtime_commands_map_to_the_right_subcommands(self):
        sub = lambda item: item["command"][3]  # noqa: E731
        assert {a["id"]: sub(a) for a in MANIFEST["actions"]} == {"start": "start", "stop": "stop", "toggle": "toggle", "status": "status", "open-preview": "open-preview"}
        assert sub(MANIFEST["events"][0]) == "on-status"
        assert sub(MANIFEST["startup"][0]) == "restore"

    def test_build_only_creates_the_venv_because_runtime_needs_only_the_stdlib(self):
        builds = [b["command"] for b in MANIFEST["build"]]
        assert builds == [["python3", "-m", "venv", ".venv"]]
        # requirements.txt holds the test-only dependencies (pytest, tomli); the build step never
        # installs them, because nothing in markmap_pipeline/ imports anything outside the stdlib.
        assert (ROOT / "requirements.txt").exists()
        assert not any("pip" in part for command in builds for part in command)

    def test_only_one_manifest_is_discoverable_by_the_marketplace(self):
        # The marketplace lists every herdr-plugin.toml in the repository as an installable plugin,
        # so the spike plugin must not carry that file name.
        manifests = [p for p in ROOT.rglob("herdr-plugin.toml") if ".venv" not in p.parts]
        assert manifests == [ROOT / "herdr-plugin.toml"]
