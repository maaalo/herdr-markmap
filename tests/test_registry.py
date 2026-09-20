"""The per-pane registry entries and the paths derived from a pane id."""
from markmap_pipeline.registry import Registry, WatchedAgent


class TestRegistry:
    def make_agent(self, pane_id="w5:p1", **kw):
        base = dict(pane_id=pane_id, cwd="/Users/x/git/proj", agent_kind="claude", label="proj", output="/Users/x/git/proj/.mindmap/proj.md", workspace_id="w5")
        base.update(kw)
        return WatchedAgent(**base)

    def test_register_get_and_unregister(self, tmp_path):
        reg = Registry(tmp_path)
        agent = self.make_agent()
        reg.register(agent)
        assert reg.get("w5:p1") == agent
        assert (tmp_path / "agents" / "w5_p1.json").exists()
        assert reg.unregister("w5:p1") is True
        assert reg.get("w5:p1") is None
        assert reg.unregister("w5:p1") is False

    def test_all_is_sorted_by_pane_id(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register(self.make_agent("w7:p2"))
        reg.register(self.make_agent("w3:p1"))
        assert [a.pane_id for a in reg.all()] == ["w3:p1", "w7:p2"]

    def test_register_overwrites_and_keeps_preview_server_fields(self, tmp_path):
        reg = Registry(tmp_path)
        reg.register(self.make_agent(preview_pid=4242, preview_port=8765, preview_url="http://localhost:8765/?key=abc&filename=proj.md"))
        got = reg.get("w5:p1")
        assert (got.preview_pid, got.preview_port) == (4242, 8765)
        assert got.preview_url.startswith("http://localhost:8765/")
        reg.update("w5:p1", preview_pid=None, preview_url=None)
        assert reg.get("w5:p1").preview_pid is None and reg.get("w5:p1").preview_port == 8765

    def test_preview_log_path_is_per_pane(self, tmp_path):
        assert Registry(tmp_path).preview_log_path("w5:p1") == tmp_path / "previews" / "w5_p1.log"

    def test_corrupt_entry_is_ignored(self, tmp_path):
        (tmp_path / "agents").mkdir()
        (tmp_path / "agents" / "bad.json").write_text("{nope")
        assert Registry(tmp_path).all() == []

    def test_state_and_lock_paths_are_per_pane(self, tmp_path):
        reg = Registry(tmp_path)
        assert reg.state_path("w5:p1") == tmp_path / "positions" / "w5_p1.state.json"
