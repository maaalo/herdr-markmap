"""Summarizer backends.

ClaudeCliBackend (default) runs `claude -p` as a one-shot process: no session is persisted, no
settings or hooks are loaded, tools are disabled and the cwd is outside the project, so the main
agent's context is untouched. AnthropicBackend calls the Anthropic SDK directly.
"""
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Union

DEFAULT_MODEL = "claude-opus-5"


class BackendError(Exception):
    """A failed model call. Callers keep the existing file and retry on the next trigger."""


class ClaudeCliBackend:
    def __init__(
        self,
        model: Optional[str] = DEFAULT_MODEL,
        scratch_dir: Optional[Union[str, Path]] = None,
        executable: str = "claude",
        timeout: float = 600.0,
        runner: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
        effort: Optional[str] = None,
    ):
        self.model = model
        self.effort = effort
        self.scratch_dir = Path(scratch_dir) if scratch_dir else Path(tempfile.gettempdir()) / "herdr-markmap-scratch"
        self.executable = executable
        self.timeout = timeout
        self.runner = runner

    def command(self, system: str) -> List[str]:
        cmd = [
            self.executable,
            "-p",
            "--no-session-persistence",  # Do not persist the session (--bare is avoided: it skips keychain access and fails to log in)
            "--setting-sources",
            "",  # Do not load user/project/local settings or hooks
            "--tools",
            "",
            "--output-format",
            "text",
            "--system-prompt",
            system,
        ]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]  # reasoning depth; lower is faster (Haiku 4.5 rejects it)
        return cmd

    def complete(self, system: str, user: str) -> str:
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        cmd = self.command(system)
        try:
            completed = self.runner(cmd, input=user, capture_output=True, text=True, timeout=self.timeout, cwd=str(self.scratch_dir))
        except FileNotFoundError:
            raise BackendError("claude command not found: %s" % self.executable)
        except subprocess.TimeoutExpired:
            raise BackendError("claude -p did not finish within %ss" % self.timeout)
        if completed.returncode != 0:
            detail = ((completed.stderr or "").strip() or (completed.stdout or "").strip())[:500]
            raise BackendError("claude -p exited with code %d: %s" % (completed.returncode, detail))
        return completed.stdout


class AnthropicBackend:
    """Needs `ANTHROPIC_API_KEY` or a logged-in profile. The SDK is imported lazily."""

    def __init__(self, model: str = DEFAULT_MODEL, client=None, max_tokens: int = 16000, effort: Optional[str] = None):
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise BackendError("anthropic package is not installed: pip install anthropic")
            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, user: str) -> str:
        kwargs = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        try:
            with self.client.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except BackendError:
            raise
        except Exception as exc:  # The SDK is imported lazily, so its exception hierarchy is folded into BackendError here
            raise BackendError("Anthropic API call failed: %s" % exc)
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise BackendError("Model declined the request (refusal): %s" % (getattr(details, "category", None) or ""))
        if message.stop_reason == "max_tokens":
            raise BackendError("Output was cut off at max_tokens=%d" % self.max_tokens)
        return "".join(block.text for block in message.content if getattr(block, "type", None) == "text")


def make_backend(name: str, model: str = DEFAULT_MODEL, effort: Optional[str] = None, scratch_dir: Optional[str] = None):
    if name == "anthropic":
        return AnthropicBackend(model=model, effort=effort)
    return ClaudeCliBackend(model=model, scratch_dir=scratch_dir, effort=effort)
