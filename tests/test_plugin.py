import json
from pathlib import Path

import pytest

from markmap_pipeline import plugin, preview
from markmap_pipeline.backends import BackendError
from markmap_pipeline.registry import Registry, WatchedAgent
from markmap_pipeline.updater import UpdateResult

FIXTURES = Path(__file__).parent / "fixtures" / "herdr_events"
ICON = "🗺"


def proj_dir(state_dir):
    """Agent cwd for tests; replaces the fixture's /Users/x/... so real files stay inside tmp."""
    return Path(state_dir).parent / "proj"


def environ_from_fixture(name, state_dir, config_dir, **overrides):
    fx = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    env = dict(fx["env"])
    context = dict(fx["context"])
    if context.get("focused_pane_cwd", "").startswith("/Users/x/"):
        context["focused_pane_cwd"] = str(proj_dir(state_dir))
        context["workspace_cwd"] = str(proj_dir(state_dir))
    env["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps(context)
    if fx["event"] is not None:
        env["HERDR_PLUGIN_EVENT_JSON"] = json.dumps(fx["event"])
    env["HERDR_PLUGIN_STATE_DIR"] = str(state_dir)
    env["HERDR_PLUGIN_CONFIG_DIR"] = str(config_dir)
    env["HERDR_PLUGIN_ID"] = "maaalo.herdr-markmap"
    env.update(overrides)
    return env


class FakeDeps(plugin.Deps):
    """Records herdr CLI calls, merges, sleeps, spawns and browser opens instead of doing them (read_log is inherited)."""

    def __init__(self, busy_ports=(), alive_pids=(), listen=True):
        self.herdr_calls = []
        self.merges = []
        self.sleeps = []
        self.spawns = []
        self.kills = []
        self.opened_urls = []
        self.busy_ports = set(busy_ports)
        self.alive_pids = set(alive_pids)
        self.listen = listen  # False simulates markmap never printing its URL
        self.die_on_ports = set()  # markmap started on these ports dies immediately with EADDRINUSE
        self.ignore_term = set()  # PIDs that ignore SIGTERM
        self.force_kills = []
        self.next_pid = 5000
        self.merge_result = UpdateResult(status="updated", attempts=1)

        self.sequence = []  # order of side effects ("merge" / "open" / "spawn")

    def notifications(self):
        return [(c[2], c[c.index("--body") + 1] if "--body" in c else "") for c in self.herdr_calls if c[:2] == ["notification", "show"]]

    def herdr(self, args):
        self.herdr_calls.append(list(args))
        return {"result": {"type": "ok"}}

    def merge(self, agent, config, env):
        self.sequence.append("merge")
        self.merges.append(agent)
        if isinstance(self.merge_result, Exception):
            raise self.merge_result
        return self.merge_result

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def port_free(self, port):
        return port not in self.busy_ports

    def spawn(self, argv, log_path):
        self.sequence.append("spawn")
        self.spawns.append((list(argv), Path(log_path)))
        self.next_pid += 1
        self.alive_pids.add(self.next_pid)
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        port = argv[argv.index("--port") + 1]
        if int(port) in self.die_on_ports:
            self.alive_pids.discard(self.next_pid)
            Path(log_path).write_text("Error: listen EADDRINUSE: address already in use :::%s\n" % port)
        elif self.listen:
            Path(log_path).write_text("Listening at http://localhost:%s/?key=abc123&filename=%s\n" % (port, Path(argv[-1]).name))
        else:
            Path(log_path).write_text("")
        return self.next_pid

    def is_alive(self, pid):
        return pid in self.alive_pids

    def kill(self, pid):
        self.kills.append(pid)
        if pid not in self.ignore_term:
            self.alive_pids.discard(pid)

    def kill_force(self, pid):
        self.force_kills.append(pid)
        self.alive_pids.discard(pid)

    def open_url(self, url):
        self.sequence.append("open")
        self.opened_urls.append(url)

    # helpers
    def token_reports(self, pane="w5:p1"):
        return [c[c.index("--token") + 1] for c in self.herdr_calls if c[:2] == ["pane", "report-metadata"] and c[2] == pane]


@pytest.fixture
def dirs(tmp_path):
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir()
    config.mkdir()
    return state, config


def registered(state, pane_id="w5:p1", **kw):
    proj = proj_dir(state)
    base = dict(pane_id=pane_id, cwd=str(proj), agent_kind="claude", label="herdr-markmap", output=str(proj / ".mindmap" / "herdr-markmap.md"), workspace_id="w5")
    base.update(kw)
    agent = WatchedAgent(**base)
    Registry(state).register(agent)
    return agent


class TestPickPort:
    def test_first_free_port_from_base(self):
        assert preview.pick_port(8765, lambda p: p not in {8765, 8766}) == 8767

    def test_gives_up_after_range(self):
        with pytest.raises(RuntimeError):
            preview.pick_port(8765, lambda p: False, attempts=3)


class TestStart:
    def test_registers_merges_starts_preview_server_and_reports_token(self, dirs, tmp_path, capsys):
        state, config = dirs
        deps = FakeDeps()
        env = environ_from_fixture("action-working.json", state, config, HERDR_PLUGIN_ACTION_ID="start")
        assert plugin.main(["start"], environ=env, deps=deps) == 0

        agent = Registry(state).get("w5:p1")
        assert agent.cwd == str(proj_dir(state))
        assert agent.output == str(proj_dir(state) / ".mindmap" / "herdr-markmap.md")
        assert Path(agent.output).exists()  # skeleton is created before markmap starts
        assert [a.pane_id for a in deps.merges] == ["w5:p1"]

        # markmap is started in the background and URL, PID and port are recorded
        argv, log = deps.spawns[0]
        assert argv[:3] == ["markmap", "-w", "--no-open"]
        assert argv[argv.index("--port") + 1] == "8765"
        assert argv[-1] == agent.output
        assert log == state / "previews" / "w5_p1.log"
        assert agent.preview_pid == 5001 and agent.preview_port == 8765
        assert agent.preview_url == "http://localhost:8765/?key=abc123&filename=herdr-markmap.md"

        # the sidebar token is reported (no pane is opened)
        report = next(c for c in deps.herdr_calls if c[:2] == ["pane", "report-metadata"])
        assert report[2] == "w5:p1"
        assert report[report.index("--source") + 1] == "plugin:mindmap"
        assert report[report.index("--token") + 1] == "mindmap=%s" % ICON
        assert not any(c[:3] == ["plugin", "pane", "open"] for c in deps.herdr_calls)

        assert (config / "config.json").exists()
        out = capsys.readouterr().out
        assert "w5:p1" in out and agent.preview_url in out

    def test_creates_skeleton_output_before_starting_markmap(self, dirs, tmp_path, monkeypatch):
        state, config = dirs
        deps = FakeDeps()
        env = environ_from_fixture("action-working.json", state, config)
        ctx = json.loads(env["HERDR_PLUGIN_CONTEXT_JSON"])
        ctx["focused_pane_cwd"] = str(tmp_path / "proj")
        env["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps(ctx)
        deps.merge_result = UpdateResult(status="noop")  # no conversation yet
        assert plugin.main(["start"], environ=env, deps=deps) == 0
        output = tmp_path / "proj" / ".mindmap" / "herdr-markmap.md"
        assert output.exists() and "# herdr-markmap" in output.read_text(encoding="utf-8")

    def test_picks_next_free_port(self, dirs):
        state, config = dirs
        deps = FakeDeps(busy_ports={8765})
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["start"], environ=env, deps=deps) == 0
        assert Registry(state).get("w5:p1").preview_port == 8766

    def test_start_without_agent_in_pane_fails(self, dirs, capsys):
        state, config = dirs
        env = environ_from_fixture("action-working.json", state, config)
        ctx = json.loads(env["HERDR_PLUGIN_CONTEXT_JSON"])
        ctx.pop("focused_pane_agent")
        env["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps(ctx)
        assert plugin.main(["start"], environ=env, deps=FakeDeps()) == 1
        assert "no agent" in capsys.readouterr().err.lower()
        assert Registry(state).all() == []

    def test_start_twice_keeps_running_server(self, dirs, capsys):
        state, config = dirs
        deps = FakeDeps()
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["start"], environ=env, deps=deps) == 0
        assert plugin.main(["start"], environ=env, deps=deps) == 0
        assert len(deps.spawns) == 1  # a live server is not restarted
        assert "already" in capsys.readouterr().out.lower()

    def test_start_again_restarts_dead_server(self, dirs):
        state, config = dirs
        registered(state, preview_pid=999, preview_port=8765, preview_url="http://localhost:8765/?key=old")
        deps = FakeDeps()  # pid 999 is not alive
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["start"], environ=env, deps=deps) == 0
        assert len(deps.spawns) == 1
        assert Registry(state).get("w5:p1").preview_pid == 5001

    def test_preview_disabled_by_config(self, dirs):
        state, config = dirs
        (config / "config.json").write_text(json.dumps({"preview": False}))
        deps = FakeDeps()
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["start"], environ=env, deps=deps) == 0
        assert deps.spawns == []
        assert Registry(state).get("w5:p1").preview_pid is None
        assert deps.token_reports() == ["mindmap=%s" % ICON]  # the token is still reported

    def test_url_falls_back_to_port_when_markmap_prints_nothing(self, dirs, capsys):
        state, config = dirs
        deps = FakeDeps(listen=False)
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["start"], environ=env, deps=deps) == 0
        assert Registry(state).get("w5:p1").preview_url == "http://localhost:8765/"
        assert len(deps.sleeps) >= 1  # waits a little for the URL

    def test_merge_failure_keeps_registration_and_reports(self, dirs, capsys):
        state, config = dirs
        deps = FakeDeps()
        deps.merge_result = BackendError("boom")
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["start"], environ=env, deps=deps) == 0
        assert Registry(state).get("w5:p1") is not None
        assert "boom" in capsys.readouterr().err


class TestActionNotifications:
    def test_start_stop_and_open_preview_show_a_notification(self, dirs):
        state, config = dirs
        deps = FakeDeps()
        env = environ_from_fixture("action-working.json", state, config)
        plugin.main(["start"], environ=env, deps=deps)
        assert deps.notifications()[-1][0].startswith("Mind map")
        assert "watching" in deps.notifications()[-1][0].lower()
        plugin.main(["open-preview"], environ=env, deps=deps)
        assert "preview" in deps.notifications()[-1][0].lower()
        plugin.main(["stop"], environ=env, deps=deps)
        assert "stopped" in deps.notifications()[-1][0].lower()

    def test_start_failure_is_notified(self, dirs):
        state, config = dirs
        deps = FakeDeps()
        env = environ_from_fixture("action-working.json", state, config)
        ctx = json.loads(env["HERDR_PLUGIN_CONTEXT_JSON"])
        ctx.pop("focused_pane_agent")
        env["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps(ctx)
        assert plugin.main(["start"], environ=env, deps=deps) == 1
        title, body = deps.notifications()[-1]
        assert "no agent" in (title + body).lower()

    def test_event_hook_does_not_notify_unless_configured(self, dirs):
        state, config = dirs
        registered(state, pane_id="w5:p4")
        deps = FakeDeps()
        env = environ_from_fixture("agent_status_changed-done.json", state, config)
        plugin.main(["on-status"], environ=env, deps=deps)
        assert deps.notifications() == []


class TestStop:
    def test_kills_server_clears_token_and_unregisters(self, dirs, capsys):
        state, config = dirs
        registered(state, preview_pid=4242, preview_port=8765, preview_url="http://localhost:8765/?key=x")
        deps = FakeDeps(alive_pids={4242})
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["stop"], environ=env, deps=deps) == 0
        assert deps.kills == [4242]
        assert deps.token_reports() == ["mindmap="]  # cleared with an empty value
        assert Registry(state).get("w5:p1") is None
        assert "herdr-markmap.md" in capsys.readouterr().out

    def test_stop_when_not_watching_is_ok(self, dirs, capsys):
        state, config = dirs
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["stop"], environ=env, deps=FakeDeps()) == 0
        assert "not watching" in capsys.readouterr().out.lower()


class TestOpenPreview:
    def test_opens_registered_url_in_browser(self, dirs):
        state, config = dirs
        registered(state, preview_pid=4242, preview_port=8765, preview_url="http://localhost:8765/?key=x&filename=m.md")
        deps = FakeDeps(alive_pids={4242})
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["open-preview"], environ=env, deps=deps) == 0
        assert deps.opened_urls == ["http://localhost:8765/?key=x&filename=m.md"]

    def test_restarts_server_if_it_died_then_opens(self, dirs):
        state, config = dirs
        registered(state, preview_pid=4242, preview_port=8765, preview_url="http://localhost:8765/?key=old")
        deps = FakeDeps()  # dead
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["open-preview"], environ=env, deps=deps) == 0
        assert len(deps.spawns) == 1
        assert deps.opened_urls == ["http://localhost:8765/?key=abc123&filename=herdr-markmap.md"]

    def test_not_watching_starts_watching_and_opens_before_the_initial_merge(self, dirs, capsys):
        state, config = dirs
        deps = FakeDeps()
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["open-preview"], environ=env, deps=deps) == 0
        agent = Registry(state).get("w5:p1")
        assert agent is not None and agent.preview_pid == 5001
        assert deps.opened_urls == [agent.preview_url]
        assert [a.pane_id for a in deps.merges] == ["w5:p1"]
        assert deps.sequence.index("open") < deps.sequence.index("merge")  # the browser opens first, the merge follows
        assert deps.token_reports() == ["mindmap=%s" % ICON]
        assert "Watching w5:p1" in capsys.readouterr().out

    def test_not_watching_and_no_agent_in_pane_fails_with_notification(self, dirs, capsys):
        state, config = dirs
        deps = FakeDeps()
        env = environ_from_fixture("action-working.json", state, config)
        ctx = json.loads(env["HERDR_PLUGIN_CONTEXT_JSON"])
        ctx.pop("focused_pane_agent")
        env["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps(ctx)
        assert plugin.main(["open-preview"], environ=env, deps=deps) == 1
        assert "no agent" in capsys.readouterr().err.lower()
        assert deps.opened_urls == []
        titles = [t for t, _ in deps.notifications()]
        assert any("Mind map" in t for t in titles)


class TestOnStatus:
    def registered_env(self, dirs, fixture):
        state, config = dirs
        registered(state, pane_id="w5:p4")
        return environ_from_fixture(fixture, state, config)

    @pytest.mark.parametrize("fixture", ["agent_status_changed-idle.json", "agent_status_changed-done.json"])
    def test_idle_or_done_for_watched_pane_triggers_merge_after_settle(self, dirs, fixture):
        deps = FakeDeps()
        assert plugin.main(["on-status"], environ=self.registered_env(dirs, fixture), deps=deps) == 0
        assert [a.pane_id for a in deps.merges] == ["w5:p4"]
        assert deps.sleeps == [1.5]

    def test_token_shows_progress_then_icon(self, dirs):
        deps = FakeDeps()
        plugin.main(["on-status"], environ=self.registered_env(dirs, "agent_status_changed-done.json"), deps=deps)
        assert deps.token_reports("w5:p4") == ["mindmap=%s …" % ICON, "mindmap=%s" % ICON]

    def test_token_shows_error_marker_on_failure(self, dirs, capsys):
        deps = FakeDeps()
        deps.merge_result = BackendError("rate limited")
        assert plugin.main(["on-status"], environ=self.registered_env(dirs, "agent_status_changed-done.json"), deps=deps) == 0
        assert deps.token_reports("w5:p4")[-1] == "mindmap=%s !" % ICON
        assert "rate limited" in capsys.readouterr().err

    @pytest.mark.parametrize("fixture", ["agent_status_changed-working.json", "agent_status_changed-blocked.json"])
    def test_other_statuses_are_ignored(self, dirs, fixture):
        deps = FakeDeps()
        assert plugin.main(["on-status"], environ=self.registered_env(dirs, fixture), deps=deps) == 0
        assert deps.merges == [] and deps.sleeps == [] and deps.herdr_calls == []

    def test_unwatched_pane_is_ignored(self, dirs):
        state, config = dirs
        deps = FakeDeps()
        env = environ_from_fixture("agent_status_changed-done.json", state, config)
        assert plugin.main(["on-status"], environ=env, deps=deps) == 0
        assert deps.merges == []

    def test_trigger_statuses_from_config(self, dirs):
        state, config = dirs
        (config / "config.json").write_text(json.dumps({"trigger_statuses": ["done"], "settle_seconds": 0}))
        deps = FakeDeps()
        assert plugin.main(["on-status"], environ=self.registered_env(dirs, "agent_status_changed-idle.json"), deps=deps) == 0
        assert deps.merges == []

    def test_notify_on_success_when_enabled(self, dirs):
        state, config = dirs
        (config / "config.json").write_text(json.dumps({"notify": True, "settle_seconds": 0}))
        deps = FakeDeps()
        plugin.main(["on-status"], environ=self.registered_env(dirs, "agent_status_changed-done.json"), deps=deps)
        assert any(c[:2] == ["notification", "show"] for c in deps.herdr_calls)


class TestStatus:
    def test_lists_watched_agents_with_urls(self, dirs, capsys):
        state, config = dirs
        registered(state, preview_url="http://localhost:8765/?key=x", preview_pid=1)
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["status"], environ=env, deps=FakeDeps(alive_pids={1})) == 0
        out = capsys.readouterr().out
        assert "w5:p1" in out and "herdr-markmap.md" in out and "http://localhost:8765/?key=x" in out and "running" in out

    def test_empty(self, dirs, capsys):
        state, config = dirs
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["status"], environ=env, deps=FakeDeps()) == 0
        assert "no agents" in capsys.readouterr().out.lower()


class TestRestore:
    def test_restarts_servers_and_re_reports_tokens(self, dirs):
        state, config = dirs
        registered(state, pane_id="w5:p1", preview_pid=111, preview_port=8765)
        registered(state, pane_id="w3:p1", label="q", output=str(proj_dir(state) / ".mindmap" / "q.md"), workspace_id="w3", preview_pid=222, preview_port=8766)
        deps = FakeDeps(alive_pids={222})  # 111 is dead, 222 is alive
        env = environ_from_fixture("action-working.json", state, config, HERDR_PLUGIN_EVENT="startup")
        assert plugin.main(["restore"], environ=env, deps=deps) == 0
        assert len(deps.spawns) == 1
        assert deps.spawns[0][0][deps.spawns[0][0].index("--port") + 1] == "8765"  # reuses the previous port
        assert deps.token_reports("w5:p1") == ["mindmap=%s" % ICON]
        assert deps.token_reports("w3:p1") == ["mindmap=%s" % ICON]

    def test_restore_without_preview_only_reports_tokens(self, dirs):
        state, config = dirs
        (config / "config.json").write_text(json.dumps({"preview": False}))
        registered(state)
        deps = FakeDeps()
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["restore"], environ=env, deps=deps) == 0
        assert deps.spawns == []
        assert deps.token_reports() == ["mindmap=%s" % ICON]


class TestUnknownCommand:
    def test_unknown_subcommand_exits_2(self, dirs, capsys):
        state, config = dirs
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["bogus"], environ=env, deps=FakeDeps()) == 2
        assert "start" in capsys.readouterr().err


class TestMergeBackendConfig:
    def test_run_merge_builds_a_medium_effort_backend_for_merges(self, dirs, tmp_path, monkeypatch):
        from markmap_pipeline import config as config_module
        from markmap_pipeline import plugin_context

        state, config = dirs
        (config / "config.json").write_text(json.dumps({"model": "claude-opus-5", "backend": "claude-cli"}))
        built = []

        class Backend:
            def complete(self, system, user):
                return "# proj\n\n## T\n- x\n"

        def fake_make_backend(name, **kw):
            built.append((name, kw))
            return Backend()

        monkeypatch.setattr(plugin, "make_backend", fake_make_backend)
        projects = tmp_path / "projects"
        (projects / "-tmp-proj").mkdir(parents=True)
        (projects / "-tmp-proj" / "s.jsonl").write_text(json.dumps({"type": "user", "message": {"content": "hi"}, "cwd": "/tmp/proj"}) + "\n")
        agent = WatchedAgent(pane_id="w5:p1", cwd="/tmp/proj", agent_kind="claude", label="proj", output=str(tmp_path / "out" / "proj.md"), workspace_id="w5")
        env = plugin_context.load_plugin_env({"HERDR_PLUGIN_STATE_DIR": str(state), "HERDR_PLUGIN_CONFIG_DIR": str(config), "HERDR_PLUGIN_ID": "maaalo.herdr-markmap"})
        plugin.run_merge(agent, config_module.load_config(config), env, projects_root=projects)
        efforts = [kw.get("effort") for _, kw in built]
        assert sorted(efforts, key=str) == ["None", "medium"] or set(efforts) == {None, "medium"}  # initial build: default effort, merges: medium
        assert all(kw.get("model") == "claude-opus-5" for _, kw in built)

    def test_run_merge_detects_the_map_language_with_the_merge_backend(self, dirs, tmp_path, monkeypatch):
        from markmap_pipeline import config as config_module
        from markmap_pipeline import plugin_context

        state, config = dirs
        (config / "config.json").write_text(json.dumps({"model": "claude-opus-5", "backend": "claude-cli"}))
        calls = []

        class Backend:
            def __init__(self, effort):
                self.effort = effort

            def complete(self, system, user):
                calls.append((self.effort, system))
                return "English" if "name of the language" in system else "# proj\n\n## T\n- x\n"

        monkeypatch.setattr(plugin, "make_backend", lambda name, **kw: Backend(kw.get("effort")))
        projects = tmp_path / "projects"
        (projects / "-tmp-proj").mkdir(parents=True)
        (projects / "-tmp-proj" / "s.jsonl").write_text(json.dumps({"type": "user", "message": {"content": "What is herdr?"}, "cwd": "/tmp/proj"}) + "\n")
        agent = WatchedAgent(pane_id="w5:p1", cwd="/tmp/proj", agent_kind="claude", label="proj", output=str(tmp_path / "out" / "proj.md"), workspace_id="w5")
        env = plugin_context.load_plugin_env({"HERDR_PLUGIN_STATE_DIR": str(state), "HERDR_PLUGIN_CONFIG_DIR": str(config), "HERDR_PLUGIN_ID": "maaalo.herdr-markmap"})
        plugin.run_merge(agent, config_module.load_config(config), env, projects_root=projects)
        detection = [c for c in calls if "name of the language" in c[1]]
        builds = [c for c in calls if "name of the language" not in c[1]]
        assert len(detection) == 1 and detection[0][0] == "medium"  # the cheaper merge setting judges the language
        assert len(builds) == 1 and "Write the entire mind map in English" in builds[0][1]


class TestRunMerge:
    def test_real_merge_uses_jsonl_source_lock_and_state_dir(self, dirs, tmp_path, monkeypatch):
        """run_merge without substitutes: resolves the JSONL source from cwd and keeps state in STATE_DIR; only the backend is fake."""
        from markmap_pipeline import config as config_module
        from markmap_pipeline import plugin_context

        state, config = dirs
        projects = tmp_path / "projects"
        cwd = "/tmp/proj"
        (projects / "-tmp-proj").mkdir(parents=True)
        (projects / "-tmp-proj" / "s.jsonl").write_text(json.dumps({"type": "user", "message": {"content": "hello"}, "cwd": cwd}) + "\n")
        output = tmp_path / "out" / "proj.md"
        agent = WatchedAgent(pane_id="w5:p1", cwd=cwd, agent_kind="claude", label="proj", output=str(output), workspace_id="w5")

        class Backend:
            def complete(self, system, user):
                return "# proj\n\n## Hello\n- hello\n"

        monkeypatch.setattr(plugin, "make_backend", lambda name, **kw: Backend())
        env = plugin_context.load_plugin_env({"HERDR_PLUGIN_STATE_DIR": str(state), "HERDR_PLUGIN_CONFIG_DIR": str(config), "HERDR_PLUGIN_ID": "maaalo.herdr-markmap"})
        result = plugin.run_merge(agent, config_module.load_config(config), env, projects_root=projects)
        assert result.status == "created"
        assert "## Hello" in output.read_text(encoding="utf-8")
        assert (state / "positions" / "w5_p1.state.json").exists()


class TestToggle:
    def test_toggle_starts_when_not_watching(self, dirs, capsys):
        state, config = dirs
        deps = FakeDeps()
        env = environ_from_fixture("action-working.json", state, config, HERDR_PLUGIN_ACTION_ID="toggle")
        assert plugin.main(["toggle"], environ=env, deps=deps) == 0
        agent = Registry(state).get("w5:p1")
        assert agent is not None and agent.preview_pid == 5001
        assert deps.token_reports() == ["mindmap=%s" % ICON]
        assert "Watching w5:p1" in capsys.readouterr().out

    def test_toggle_stops_when_watching(self, dirs, capsys):
        state, config = dirs
        registered(state, preview_pid=4242, preview_port=8765, preview_url="http://localhost:8765/?key=x")
        deps = FakeDeps(alive_pids={4242})
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["toggle"], environ=env, deps=deps) == 0
        assert Registry(state).get("w5:p1") is None
        assert deps.kills == [4242]
        assert deps.token_reports() == ["mindmap="]
        assert "Stopped watching w5:p1" in capsys.readouterr().out

    def test_toggle_without_agent_fails_like_start(self, dirs, capsys):
        state, config = dirs
        env = environ_from_fixture("action-working.json", state, config)
        ctx = json.loads(env["HERDR_PLUGIN_CONTEXT_JSON"])
        ctx.pop("focused_pane_agent")
        env["HERDR_PLUGIN_CONTEXT_JSON"] = json.dumps(ctx)
        assert plugin.main(["toggle"], environ=env, deps=FakeDeps()) == 1
        assert "no agent" in capsys.readouterr().err.lower()


class TestPreviewUrlRecovery:
    def test_waits_up_to_fifteen_seconds_for_the_url(self, dirs):
        state, config = dirs
        deps = FakeDeps(listen=False)
        env = environ_from_fixture("action-working.json", state, config)
        plugin.main(["start"], environ=env, deps=deps)
        assert abs(sum(deps.sleeps) - 15.0) < 0.5  # observed: markmap can take a few seconds to print its URL

    def test_open_preview_refreshes_a_fallback_url_from_the_log(self, dirs):
        state, config = dirs
        agent = registered(state, preview_pid=4242, preview_port=8765, preview_url="http://localhost:8765/")
        deps = FakeDeps(alive_pids={4242})
        log = Registry(state).preview_log_path("w5:p1")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("Listening at http://localhost:8765/?key=late&filename=herdr-markmap.md\n")
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["open-preview"], environ=env, deps=deps) == 0
        assert deps.opened_urls == ["http://localhost:8765/?key=late&filename=herdr-markmap.md"]
        assert Registry(state).get("w5:p1").preview_url.endswith("key=late&filename=herdr-markmap.md")
        assert deps.spawns == []  # a live server is not restarted

    def test_open_preview_restarts_server_when_url_is_still_unknown(self, dirs):
        state, config = dirs
        registered(state, preview_pid=4242, preview_port=8765, preview_url="http://localhost:8765/")
        deps = FakeDeps(alive_pids={4242})  # no URL in the log
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["open-preview"], environ=env, deps=deps) == 0
        assert deps.kills == [4242] and len(deps.spawns) == 1
        assert deps.opened_urls == ["http://localhost:8765/?key=abc123&filename=herdr-markmap.md"]

    def test_status_refreshes_fallback_url(self, dirs, capsys):
        state, config = dirs
        registered(state, preview_pid=4242, preview_port=8765, preview_url="http://localhost:8765/")
        log = Registry(state).preview_log_path("w5:p1")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("Listening at http://localhost:8765/?key=late&filename=herdr-markmap.md\n")
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["status"], environ=env, deps=FakeDeps(alive_pids={4242})) == 0
        assert "key=late" in capsys.readouterr().out


class TestPortAndProcessRobustness:
    def test_real_port_free_detects_an_ipv6_wildcard_listener(self):
        """Observed: markmap (hono) listens on ::; a 127.0.0.1 bind with SO_REUSEADDR wrongly reported the port as free."""
        import socket

        srv = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            srv.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        srv.bind(("::", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            assert plugin.Deps().port_free(port) is False
        finally:
            srv.close()
        assert plugin.Deps().port_free(port) is True

    def test_start_retries_on_next_port_when_markmap_dies_immediately(self, dirs, capsys):
        state, config = dirs
        deps = FakeDeps()
        deps.die_on_ports = {8765}
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["start"], environ=env, deps=deps) == 0
        ports = [argv[argv.index("--port") + 1] for argv, _ in deps.spawns]
        assert ports == ["8765", "8766"]
        agent = Registry(state).get("w5:p1")
        assert agent.preview_port == 8766 and "8766" in agent.preview_url
        assert "EADDRINUSE" in capsys.readouterr().err

    def test_stop_force_kills_a_server_that_ignores_sigterm(self, dirs):
        state, config = dirs
        registered(state, preview_pid=4242, preview_port=8765, preview_url="http://localhost:8765/?key=x")
        deps = FakeDeps(alive_pids={4242})
        deps.ignore_term = {4242}
        env = environ_from_fixture("action-working.json", state, config)
        assert plugin.main(["stop"], environ=env, deps=deps) == 0
        assert deps.kills == [4242]
        assert deps.force_kills == [4242]
