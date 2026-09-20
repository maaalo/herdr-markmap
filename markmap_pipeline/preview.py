"""Management of the background live-preview process (markmap -w).

markmap prints "Listening at <URL>" on startup; the URL carries key and filename parameters and a URL
without them returns 404. Side effects (spawn, liveness, kill, port checks, sleep, log reads) go through
the `ops` argument, a system.SystemOps (plugin.Deps in production), which tests substitute.
"""
import sys
from pathlib import Path
from typing import Callable, Optional

from .files import atomic_write
from .markdown import initial_document
from .config import Config
from .registry import Registry, WatchedAgent

URL_WAIT_ATTEMPTS = 75  # at 0.2s intervals = up to 15s
URL_WAIT_INTERVAL = 0.2
PORT_RETRIES = 3  # retries on the next port when markmap dies right after start (EADDRINUSE etc.)
STOP_WAIT_ATTEMPTS = 20  # at 0.1s intervals = SIGKILL after 2s


def pick_port(base: int, port_free: Callable[[int], bool], attempts: int = 50) -> int:
    for port in range(base, base + attempts):
        if port_free(port):
            return port
    raise RuntimeError("No free port between %d and %d" % (base, base + attempts - 1))


def parse_markmap_url(text: str) -> Optional[str]:
    for line in text.splitlines():
        if "Listening at " in line:
            return line.split("Listening at ", 1)[1].strip()
    return None


def url_is_complete(url: Optional[str]) -> bool:
    return bool(url) and "key=" in url


def ensure_output_exists(agent: WatchedAgent) -> None:
    """markmap -w cannot start without its input file, so create the skeleton when missing."""
    if not Path(agent.output).exists():
        atomic_write(agent.output, initial_document(agent.label or agent.pane_id))


def is_running(agent: WatchedAgent, ops) -> bool:
    return bool(agent.preview_pid and ops.is_alive(agent.preview_pid))


def refresh_url(agent: WatchedAgent, registry: Registry, ops) -> WatchedAgent:
    """If the URL was not captured at start, read the markmap log again later to fill it in."""
    if url_is_complete(agent.preview_url):
        return agent
    url = parse_markmap_url(ops.read_log(registry.preview_log_path(agent.pane_id)))
    if url:
        return registry.update(agent.pane_id, preview_url=url) or agent
    return agent


def _wait_for_url(pid: int, log_path: Path, ops) -> Optional[str]:
    """Wait for the URL; return None immediately if the process dies."""
    for _ in range(URL_WAIT_ATTEMPTS):
        url = parse_markmap_url(ops.read_log(log_path))
        if url:
            return url
        if not ops.is_alive(pid):
            return None
        ops.sleep(URL_WAIT_INTERVAL)
    return None


def start(agent: WatchedAgent, config: Config, registry: Registry, ops) -> WatchedAgent:
    """Start markmap in the background and record PID, port and URL in the registry; no-op if it is alive.

    If it dies right after start (port conflict etc.), retry on the next port.
    """
    if is_running(agent, ops):
        return refresh_url(agent, registry, ops)
    ensure_output_exists(agent)
    log_path = registry.preview_log_path(agent.pane_id)
    port = agent.preview_port if agent.preview_port and ops.port_free(agent.preview_port) else pick_port(config.preview_port_base, ops.port_free)
    for attempt in range(PORT_RETRIES):
        if log_path.exists():
            log_path.unlink()
        pid = ops.spawn(["markmap", "-w", "--no-open", "--port", str(port), agent.output], log_path)
        # Record the PID first so a stop during a long merge cannot orphan the server
        agent = registry.update(agent.pane_id, preview_pid=pid, preview_port=port, preview_url=None) or agent
        url = _wait_for_url(pid, log_path, ops)
        if url:
            return registry.update(agent.pane_id, preview_url=url) or agent
        if not ops.is_alive(pid) and attempt < PORT_RETRIES - 1:
            print("Warning: markmap exited right after start on port %d; trying the next port. Log:\n%s" % (port, ops.read_log(log_path).strip()[-600:]), file=sys.stderr)
            port = pick_port(port + 1, ops.port_free)
            continue
        break
    url = "http://localhost:%d/" % port
    print("Warning: markmap did not report its URL yet; using %s" % url, file=sys.stderr)
    return registry.update(agent.pane_id, preview_url=url) or agent


def stop(agent: WatchedAgent, ops) -> None:
    """Send SIGTERM, then SIGKILL if the process is still alive."""
    pid = agent.preview_pid
    if not pid or not ops.is_alive(pid):
        return
    ops.kill(pid)
    for _ in range(STOP_WAIT_ATTEMPTS):
        if not ops.is_alive(pid):
            return
        ops.sleep(0.1)
    ops.kill_force(pid)


def restart_if_url_unknown(agent: WatchedAgent, config: Config, registry: Registry, ops) -> WatchedAgent:
    """A live server whose URL is unknown would 404 when opened, so rebuild it."""
    agent = start(agent, config, registry, ops)
    if url_is_complete(agent.preview_url):
        return agent
    stop(agent, ops)
    agent = registry.update(agent.pane_id, preview_pid=None) or agent
    return start(agent, config, registry, ops)
