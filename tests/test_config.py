"""Plugin configuration: defaults, hand-edited overrides and where a pane's mind map is written."""
import json
from pathlib import Path

from markmap_pipeline.config import Config, default_output_path, load_config


class TestConfig:
    def test_defaults(self, tmp_path):
        cfg = load_config(tmp_path)
        assert cfg == Config()
        assert cfg.model == "claude-opus-5"
        assert cfg.backend == "claude-cli"
        assert cfg.settle_seconds == 1.5
        assert cfg.trigger_statuses == ("idle", "done")
        assert cfg.preview is True
        assert cfg.notify is False
        assert cfg.output_dir is None
        assert cfg.preview_port_base == 8765
        assert cfg.effort is None
        assert cfg.merge_effort == "medium"
        assert cfg.sidebar_token == "mindmap"
        assert cfg.sidebar_icon == "🗺"

    def test_overrides_from_config_json(self, tmp_path):
        (tmp_path / "config.json").write_text(json.dumps({"model": "claude-haiku-4-5", "settle_seconds": 3, "output_dir": "~/mindmaps", "notify": True, "trigger_statuses": ["done"], "preview_port_base": 9000, "sidebar_icon": "M", "merge_effort": "low"}))
        cfg = load_config(tmp_path)
        assert cfg.model == "claude-haiku-4-5"
        assert cfg.merge_effort == "low"
        assert cfg.preview_port_base == 9000 and cfg.sidebar_icon == "M"
        assert cfg.settle_seconds == 3.0
        assert cfg.output_dir == "~/mindmaps"
        assert cfg.notify is True
        assert cfg.trigger_statuses == ("done",)

    def test_invalid_json_falls_back_to_defaults(self, tmp_path):
        (tmp_path / "config.json").write_text("{broken")
        assert load_config(tmp_path) == Config()

    def test_write_defaults_creates_editable_file_once(self, tmp_path):
        cfg_path = Config().write_defaults(tmp_path)
        assert cfg_path == tmp_path / "config.json"
        data = json.loads(cfg_path.read_text())
        assert data["model"] == "claude-opus-5"
        cfg_path.write_text(json.dumps({"model": "custom"}))
        Config().write_defaults(tmp_path)  # an existing file is not overwritten
        assert json.loads(cfg_path.read_text()) == {"model": "custom"}


class TestDefaultOutputPath:
    def test_uses_agent_cwd_and_workspace_label(self):
        path = default_output_path(Config(), cwd="/Users/x/git/proj", label="my proj", pane_id="w5:p1")
        assert path == Path("/Users/x/git/proj/.mindmap/my_proj.md")

    def test_falls_back_to_pane_id_without_label(self):
        assert default_output_path(Config(), cwd="/p", label=None, pane_id="w5:p1") == Path("/p/.mindmap/w5_p1.md")

    def test_output_dir_override_with_tilde(self, monkeypatch):
        monkeypatch.setenv("HOME", "/home/u")
        path = default_output_path(Config(output_dir="~/mindmaps"), cwd="/p", label="proj", pane_id="w5:p1")
        assert path == Path("/home/u/mindmaps/proj.md")
