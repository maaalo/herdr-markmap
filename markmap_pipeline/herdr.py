"""Thin wrapper around the herdr CLI.

Nothing is ever sent to the main agent. Tests inject `runner` so no real process is started.
When running as a plugin, herdr passes HERDR_BIN_PATH, which takes precedence.
"""
import json
import os
import subprocess
from typing import Callable, List, Sequence

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class HerdrError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def herdr_bin() -> str:
    """The herdr executable; herdr passes HERDR_BIN_PATH when running a plugin command."""
    return os.environ.get("HERDR_BIN_PATH") or "herdr"


def _run(cmd: List[str], runner: Runner, timeout: float) -> str:
    try:
        completed = runner(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise HerdrError("herdr_not_installed", "herdr command not found; check your PATH")
    except subprocess.TimeoutExpired:
        raise HerdrError("herdr_timeout", "herdr did not respond within %ss: %s" % (timeout, " ".join(cmd)))
    if completed.returncode != 0:
        raise _error_from_output((completed.stderr or "").strip() or (completed.stdout or "").strip(), cmd)
    return completed.stdout


def _error_from_output(detail: str, cmd: List[str]) -> HerdrError:
    """herdr reports failures as `{"error": {"code", "message"}}` JSON; otherwise attach the raw text."""
    try:
        error = json.loads(detail).get("error", {})
        if isinstance(error, dict) and error.get("code"):
            return HerdrError(error["code"], error.get("message") or error["code"])
    except ValueError:
        pass
    return HerdrError("herdr_failed", "herdr command failed (%s): %s" % (" ".join(cmd), detail[:300]))


def run_json(args: Sequence[str], runner: Runner = subprocess.run, timeout: float = 30.0) -> dict:
    """Run `herdr <args>` and return the JSON response as a dict ({} when empty or not JSON). Failures raise HerdrError."""
    stdout = _run([herdr_bin()] + list(args), runner, timeout)
    try:
        return json.loads(stdout) if stdout.strip() else {}
    except ValueError:
        return {}


def agent_read(
    target: str,
    lines: int = 400,
    source: str = "recent-unwrapped",
    runner: Runner = subprocess.run,
    timeout: float = 10.0,
) -> str:
    """Fetch a terminal snapshot as plain text via `herdr agent read` (for agents other than Claude Code)."""
    cmd = [herdr_bin(), "agent", "read", target, "--source", source, "--lines", str(lines), "--format", "text"]
    return _run(cmd, runner, timeout)
