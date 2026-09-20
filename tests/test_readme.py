"""README covers installation, every plugin action, configuration keys, and the known limitations."""
import re
from pathlib import Path

from markmap_pipeline.plugin import COMMANDS
from markmap_pipeline.config import Config

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")


class TestReadme:
    def test_is_written_in_english(self):
        lines = [l for l in README.splitlines() if CJK.search(l)]
        assert lines == [], lines

    def test_documents_every_user_facing_action(self):
        for action in ("start", "stop", "toggle", "status", "open-preview"):
            assert "maaalo.herdr-markmap.%s" % action in README, action
        assert set(COMMANDS) - {"on-status", "restore"} == {"start", "stop", "toggle", "status", "open-preview"}

    def test_documents_install_link_and_setup(self):
        for needle in (
            "herdr plugin install",
            "herdr plugin link",
            "npm install -g markmap-cli",
            "python3 -m venv .venv",
            "herdr plugin log list",
            "plugin_action",
            "[ui.sidebar.agents]",
            "$mindmap",
        ):
            assert needle in README, needle

    def test_documents_every_config_key(self):
        for key in Config().__dataclass_fields__:
            assert "`%s`" % key in README, key

    def test_shows_the_screenshot_and_it_is_in_the_repository(self):
        embedded = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", README)
        assert embedded, "the README should show what the plugin looks like in use"
        for relative_path in embedded:
            assert (ROOT / relative_path).is_file(), relative_path

    def test_documents_non_interference_and_limitations(self):
        for needle in ("--no-session-persistence", "--bare", "ANTHROPIC_API_KEY", "fcntl", "0.8.2"):
            assert needle in README, needle
