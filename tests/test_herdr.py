import json
import subprocess

import pytest

from markmap_pipeline.herdr import HerdrError, agent_read, herdr_bin, run_json

NOT_FOUND = json.dumps({"error": {"code": "agent_not_found", "message": "agent target nosuch not found"}, "id": "cli:agent:read"})


def fake_runner(returncode=0, stdout="", stderr=""):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    run.calls = calls
    return run


class TestAgentRead:
    def test_returns_text_and_passes_options(self):
        run = fake_runner(stdout="line1\nline2\n")
        text = agent_read("worker", lines=200, runner=run)
        assert text == "line1\nline2\n"
        assert run.calls == [["herdr", "agent", "read", "worker", "--source", "recent-unwrapped", "--lines", "200", "--format", "text"]]

    def test_herdr_json_error_becomes_coded_exception(self):
        run = fake_runner(returncode=1, stderr=NOT_FOUND)
        with pytest.raises(HerdrError) as exc:
            agent_read("nosuch", runner=run)
        assert exc.value.code == "agent_not_found"
        assert "nosuch" in str(exc.value)

    def test_non_json_failure_raises_herdr_error(self):
        run = fake_runner(returncode=1, stderr="connection refused")
        with pytest.raises(HerdrError) as exc:
            agent_read("x", runner=run)
        assert exc.value.code == "herdr_failed"
        assert "connection refused" in str(exc.value)

    def test_missing_binary_raises_herdr_error(self):
        def run(cmd, **kwargs):
            raise FileNotFoundError("herdr")

        with pytest.raises(HerdrError) as exc:
            agent_read("x", runner=run)
        assert exc.value.code == "herdr_not_installed"

    def test_timeout_raises_herdr_error(self):
        def run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 10)

        with pytest.raises(HerdrError) as exc:
            agent_read("x", runner=run)
        assert exc.value.code == "herdr_timeout"


class TestRunJson:
    def test_parses_json_response(self):
        run = fake_runner(stdout='{"id":"x","result":{"type":"ok"}}')
        assert run_json(["pane", "focus", "w1:p1"], runner=run) == {"id": "x", "result": {"type": "ok"}}
        assert run.calls == [["herdr", "pane", "focus", "w1:p1"]]

    def test_empty_or_non_json_output_gives_empty_dict(self):
        assert run_json(["x"], runner=fake_runner(stdout="")) == {}
        assert run_json(["x"], runner=fake_runner(stdout="plain text")) == {}

    def test_error_json_on_stdout_is_used_when_stderr_is_empty(self):
        run = fake_runner(returncode=1, stdout=NOT_FOUND, stderr="")
        with pytest.raises(HerdrError) as exc:
            run_json(["agent", "get", "nosuch"], runner=run)
        assert exc.value.code == "agent_not_found"


class TestHerdrBin:
    def test_defaults_to_herdr_on_path(self, monkeypatch):
        monkeypatch.delenv("HERDR_BIN_PATH", raising=False)
        assert herdr_bin() == "herdr"

    def test_uses_herdr_bin_path_when_set(self, monkeypatch):
        monkeypatch.setenv("HERDR_BIN_PATH", "/opt/herdr/bin/herdr")
        assert herdr_bin() == "/opt/herdr/bin/herdr"

    def test_agent_read_calls_the_configured_binary(self, monkeypatch):
        monkeypatch.setenv("HERDR_BIN_PATH", "/opt/herdr/bin/herdr")
        run = fake_runner(stdout="x\n")
        agent_read("worker", runner=run)
        assert run.calls[0][0] == "/opt/herdr/bin/herdr"
