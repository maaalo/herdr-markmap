"""Operating-system side effects: ports, background processes, signals, log files, the browser.

Everything here touches the machine, so it is kept apart from the logic in preview.py and plugin.py,
which receive an instance (plugin.Deps extends it) and are tested with a substitute.
"""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import List


class SystemOps:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def port_free(self, port: int) -> bool:
        """In use if a connection succeeds; also require a bind on IPv6 and IPv4 without SO_REUSEADDR.

        Observed: markmap (hono) listens on IPv6 `::`, so a 127.0.0.1 bind with SO_REUSEADDR succeeded even
        while the port was taken, and the new markmap died with EADDRINUSE.
        """
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return False
        except OSError:
            pass
        for family, address in ((socket.AF_INET6, ("::", port)), (socket.AF_INET, ("0.0.0.0", port))):
            try:
                with socket.socket(family, socket.SOCK_STREAM) as s:
                    s.bind(address)
            except OSError:
                return False
        return True

    def spawn(self, argv: List[str], log_path: Path) -> int:
        """Start in a new session so the process outlives the action that spawned it."""
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
        return proc.pid

    def is_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def kill(self, pid: int) -> None:
        self._signal(pid, signal.SIGTERM)

    def kill_force(self, pid: int) -> None:
        self._signal(pid, signal.SIGKILL)

    @staticmethod
    def _signal(pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)
        except OSError:
            pass

    def read_log(self, path: Path) -> str:
        return Path(path).read_text(encoding="utf-8", errors="replace") if Path(path).exists() else ""

    def open_url(self, url: str) -> None:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
