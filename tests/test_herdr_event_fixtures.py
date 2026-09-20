"""The real herdr event/context fixtures captured by the spike have the shape the plugin relies on."""
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "herdr_events"
STATUS_FILES = sorted(FIXTURES.glob("agent_status_changed-*.json"))


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_all_expected_fixtures_exist():
    names = {p.name for p in FIXTURES.iterdir()}
    for expected in (
        "action-working.json",
        "agent_detected.json",
        "agent_status_changed-blocked.json",
        "agent_status_changed-idle.json",
        "agent_status_changed-working.json",
        "agent_status_changed-done.json",
    ):
        assert expected in names


@pytest.mark.parametrize("path", STATUS_FILES, ids=lambda p: p.stem)
def test_status_change_event_carries_pane_and_status(path):
    fx = json.loads(path.read_text(encoding="utf-8"))
    data = fx["event"]["data"]
    assert fx["env"]["HERDR_PLUGIN_EVENT"] == "pane.agent_status_changed"
    assert fx["event"]["event"] == "pane_agent_status_changed"
    assert data["pane_id"] == fx["env"]["HERDR_PANE_ID"]
    assert data["agent_status"] == path.stem.split("-", 1)[1]
    assert data["agent"] == "claude"
    assert data["workspace_id"] == fx["env"]["HERDR_WORKSPACE_ID"]


@pytest.mark.parametrize("path", STATUS_FILES, ids=lambda p: p.stem)
def test_event_context_describes_the_event_pane_not_the_focused_pane(path):
    fx = json.loads(path.read_text(encoding="utf-8"))
    ctx = fx["context"]
    # The event context describes the pane the event happened in (the UI focus was elsewhere when captured)
    assert ctx["focused_pane_id"] == fx["event"]["data"]["pane_id"]
    assert ctx["focused_pane_status"] == fx["event"]["data"]["agent_status"]
    assert ctx["focused_pane_cwd"]  # cwd used to resolve the JSONL
    assert ctx["workspace_label"]  # label used for the output file name
    assert ctx["invocation_source"] == "api"


def test_action_context_has_agent_cwd_label_and_source():
    ctx = load("action-working.json")["context"]
    assert ctx["invocation_source"] == "cli"
    assert ctx["focused_pane_agent"] == "claude"
    assert ctx["focused_pane_cwd"].endswith("herdr-markmap")
    assert ctx["workspace_label"] == "herdr-markmap"
    assert load("action-working.json")["event"] is None


def test_idle_and_done_both_occur_after_a_turn():
    statuses = {p.stem.split("-", 1)[1] for p in STATUS_FILES}
    assert {"idle", "done"} <= statuses
