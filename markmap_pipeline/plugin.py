"""Entry point of the herdr plugin.

    python -m markmap_pipeline.plugin <start|stop|toggle|status|on-status|open-preview|restore>

Invoked by the commands declared in herdr-plugin.toml. Side effects (herdr CLI, merging, and the OS
operations from system.py) are bundled in `Deps` and substituted in unit tests.
"""
import datetime
import logging
import os
import sys
from pathlib import Path
from typing import Callable, List, Mapping, Optional

from . import preview
from .backends import BackendError, make_backend
from .config import Config, config_path, default_output_path, load_config
from .herdr import HerdrError, run_json
from .pipeline import LockError, Pipeline, acquire_lock
from .plugin_context import PluginEnv, load_plugin_env
from .registry import Registry, WatchedAgent
from .sources import DEFAULT_PROJECTS_ROOT, ClaudeJsonlSource, HerdrReadSource
from .system import SystemOps
from .updater import UpdateResult, Updater

COMMANDS = ("start", "stop", "toggle", "status", "on-status", "open-preview", "restore")
TOKEN_SOURCE = "plugin:mindmap"
NO_AGENT_MESSAGE = "No agent in the current pane (pane=%s). Run this action from the pane that hosts the agent."


class Deps(SystemOps):
    """Side effects of the plugin commands: the OS operations plus the Herdr CLI and the merge itself. Tests substitute it."""

    def herdr(self, args: List[str]) -> dict:
        return run_json(args)

    def merge(self, agent: WatchedAgent, config: Config, env: PluginEnv) -> UpdateResult:
        return run_merge(agent, config, env)


def run_merge(agent: WatchedAgent, config: Config, env: PluginEnv, projects_root: Path = DEFAULT_PROJECTS_ROOT) -> UpdateResult:
    """Merge the pending turns of a watched agent once, serialized by the per-output lock."""
    if agent.agent_kind == "claude":
        source = ClaudeJsonlSource(agent.cwd, projects_root=projects_root)
    else:
        source = HerdrReadSource(agent.pane_id)
    backend = make_backend(config.backend, model=config.model, effort=config.effort, scratch_dir=config.scratch_dir)
    merge_backend = make_backend(config.backend, model=config.model, effort=config.merge_effort or config.effort, scratch_dir=config.scratch_dir)
    updater = Updater(
        backend,
        agent.output,
        agent_name=agent.label or agent.pane_id,
        max_retries=config.max_retries,
        instant_placeholder=config.instant_placeholder,
        merge_backend=merge_backend,
        language_backend=merge_backend,  # the language question needs little reasoning
    )
    lock = acquire_lock(agent.output, timeout=config.lock_timeout)
    try:
        return Pipeline(source, updater, Registry(env.state_dir).state_path(agent.pane_id)).run_update()
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Feedback through herdr (notifications, sidebar token)
# ---------------------------------------------------------------------------


def notify(deps: Deps, title: str, body: str = "") -> None:
    """Show an action result as a herdr notification (action stdout is not shown in the UI)."""
    args = ["notification", "show", title[:80]]
    if body:
        args += ["--body", body[:240]]
    args += ["--sound", "none"]
    try:
        deps.herdr(args)
    except HerdrError as exc:
        print("Warning: notification failed: %s" % exc, file=sys.stderr)


def report_token(pane_id: str, value: str, config: Config, deps: Deps) -> None:
    """Update the sidebar $<token>; an empty value clears it. Failures are only warnings."""
    try:
        deps.herdr(["pane", "report-metadata", pane_id, "--source", TOKEN_SOURCE, "--token", "%s=%s" % (config.sidebar_token, value)])
    except HerdrError as exc:
        print("Warning: could not update sidebar token for %s: %s" % (pane_id, exc), file=sys.stderr)


def _fail(deps: Deps, message: str) -> int:
    print(message, file=sys.stderr)
    notify(deps, "Mind map: %s" % message.split(".")[0][:60], message)
    return 1


def _no_agent(env: PluginEnv, deps: Deps) -> int:
    return _fail(deps, NO_AGENT_MESSAGE % (env.pane_id or "?"))


def _display_name(agent: WatchedAgent) -> str:
    return agent.label or agent.pane_id


def _merge_and_report(agent: WatchedAgent, config: Config, env: PluginEnv, deps: Deps) -> Optional[UpdateResult]:
    """Run the merge; on failure print to stderr and return None."""
    try:
        result = deps.merge(agent, config, env)
    except (BackendError, LockError, HerdrError) as exc:
        print("Merge failed for %s: %s" % (agent.pane_id, exc), file=sys.stderr)
        return None
    if result.status == "error":
        print("Merge failed for %s: %s" % (agent.pane_id, result.message), file=sys.stderr)
        return None
    if result.status != "noop":
        print("%s: %s -> %s" % (agent.pane_id, result.status, agent.output))
    return result


# ---------------------------------------------------------------------------
# Registration and bring-up
# ---------------------------------------------------------------------------


def _register(env: PluginEnv, config: Config, registry: Registry) -> Optional[WatchedAgent]:
    """Register the agent in the current pane (or return the existing entry). None when the pane has no agent."""
    if not env.pane_id or not env.agent_kind:
        return None
    Config.write_defaults(env.config_dir)
    agent = registry.get(env.pane_id)
    if agent is not None:
        print("Already watching %s -> %s" % (agent.pane_id, agent.output))
        return agent
    cwd = env.agent_cwd or os.getcwd()
    agent = WatchedAgent(
        pane_id=env.pane_id,
        cwd=cwd,
        agent_kind=env.agent_kind,
        label=env.workspace_label,
        output=str(default_output_path(config, cwd, env.workspace_label, env.pane_id)),
        workspace_id=env.workspace_id,
        registered_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
    registry.register(agent)
    print("Watching %s (%s in %s)" % (agent.pane_id, agent.agent_kind, agent.cwd))
    print("  mind map : %s" % agent.output)
    print("  config   : %s" % config_path(env.config_dir))
    return agent


def _bring_up(agent: WatchedAgent, config: Config, registry: Registry, deps: Deps, repair_url: bool = False) -> WatchedAgent:
    """Make sure the output skeleton, the preview server and the sidebar token are in place."""
    preview.ensure_output_exists(agent)
    if config.preview or repair_url:
        starter = preview.restart_if_url_unknown if repair_url else preview.start
        agent = starter(agent, config, registry, deps)
        print("  preview  : %s  (action 'Mind map: open preview in browser' opens it)" % agent.preview_url)
    report_token(agent.pane_id, config.sidebar_icon, config, deps)
    return agent


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_start(env: PluginEnv, config: Config, registry: Registry, deps: Deps) -> int:
    agent = _register(env, config, registry)
    if agent is None:
        return _no_agent(env, deps)
    agent = _bring_up(agent, config, registry, deps)
    notify(deps, "Mind map: watching %s" % _display_name(agent), agent.output)
    _merge_and_report(agent, config, env, deps)
    return 0


def cmd_stop(env: PluginEnv, config: Config, registry: Registry, deps: Deps) -> int:
    agent = registry.get(env.pane_id or "")
    if agent is None:
        print("Not watching pane %s." % (env.pane_id or "?"))
        return 0
    preview.stop(agent, deps)
    report_token(agent.pane_id, "", config, deps)
    registry.unregister(agent.pane_id)
    print("Stopped watching %s. Mind map kept at %s" % (agent.pane_id, agent.output))
    notify(deps, "Mind map: stopped watching %s" % _display_name(agent), "Mind map kept at %s" % agent.output)
    return 0


def cmd_toggle(env: PluginEnv, config: Config, registry: Registry, deps: Deps) -> int:
    """stop when watching, start otherwise, so one keybinding covers both."""
    if env.pane_id and registry.get(env.pane_id) is not None:
        return cmd_stop(env, config, registry, deps)
    return cmd_start(env, config, registry, deps)


def cmd_status(env: PluginEnv, config: Config, registry: Registry, deps: Deps) -> int:
    agents = registry.all()
    if not agents:
        print("No agents are being watched. Run the 'start' action from an agent's pane.")
        return 0
    for agent in agents:
        agent = preview.refresh_url(agent, registry, deps)
        print("%s  %s  %s" % (agent.pane_id, agent.agent_kind, agent.output))
        print("    label=%s cwd=%s" % (agent.label or "-", agent.cwd))
        print("    preview=%s (%s)" % (agent.preview_url or "-", "running" if preview.is_running(agent, deps) else "stopped"))
    return 0


def cmd_on_status(env: PluginEnv, config: Config, registry: Registry, deps: Deps) -> int:
    if env.event_status not in config.trigger_statuses:
        return 0
    agent = registry.get(env.pane_id or "")
    if agent is None:
        return 0
    if config.settle_seconds > 0:
        deps.sleep(config.settle_seconds)  # Let the last history line finish being written
    report_token(agent.pane_id, config.sidebar_icon + " …", config, deps)
    result = _merge_and_report(agent, config, env, deps)
    report_token(agent.pane_id, config.sidebar_icon if result is not None else config.sidebar_icon + " !", config, deps)
    if result is not None and result.status != "noop" and config.notify:
        notify(deps, "Mind map updated", _display_name(agent))
    return 0


def cmd_open_preview(env: PluginEnv, config: Config, registry: Registry, deps: Deps) -> int:
    """Open the preview in the browser; for an unwatched pane, start watching, open the browser, then run the initial merge."""
    was_watching = registry.get(env.pane_id or "") is not None
    agent = _register(env, config, registry)
    if agent is None:
        return _no_agent(env, deps)
    agent = _bring_up(agent, config, registry, deps, repair_url=True)
    print("Opening %s" % agent.preview_url)
    deps.open_url(agent.preview_url)
    notify(deps, "Mind map: opening preview for %s" % _display_name(agent), agent.preview_url)
    if not was_watching:
        _merge_and_report(agent, config, env, deps)  # The browser reloads on its own
    return 0


def cmd_restore(env: PluginEnv, config: Config, registry: Registry, deps: Deps) -> int:
    for agent in registry.all():
        if config.preview:
            agent = preview.start(agent, config, registry, deps)
            print("Restored preview for %s at %s" % (agent.pane_id, agent.preview_url))
        report_token(agent.pane_id, config.sidebar_icon, config, deps)  # Tokens are cleared on server restart, so report them again
    return 0


HANDLERS: Mapping[str, Callable[[PluginEnv, Config, Registry, Deps], int]] = {
    "start": cmd_start,
    "stop": cmd_stop,
    "toggle": cmd_toggle,
    "status": cmd_status,
    "on-status": cmd_on_status,
    "open-preview": cmd_open_preview,
    "restore": cmd_restore,
}


def main(argv: Optional[List[str]] = None, environ: Optional[Mapping[str, str]] = None, deps: Optional[Deps] = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] not in HANDLERS:
        print("Usage: python -m markmap_pipeline.plugin <%s>" % "|".join(COMMANDS), file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    env = load_plugin_env(environ)
    config = load_config(env.config_dir)
    registry = Registry(env.state_dir)
    try:
        return HANDLERS[argv[0]](env, config, registry, deps or Deps())
    except HerdrError as exc:
        print("herdr error [%s]: %s" % (exc.code, exc), file=sys.stderr)
        return 3
    except RuntimeError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
