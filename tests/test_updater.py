import os
import subprocess
from pathlib import Path

import pytest

from markmap_pipeline.extract import Turn
from markmap_pipeline.backends import AnthropicBackend, BackendError, ClaudeCliBackend, make_backend
from markmap_pipeline.files import atomic_write
from markmap_pipeline.markdown import (
    clean_model_output,
    headings,
    initial_document,
    is_skeleton,
    join_frontmatter,
    root_count,
    split_frontmatter,
)
from markmap_pipeline.patch import fallback_append
from markmap_pipeline.prompts import INITIAL_SYSTEM
from markmap_pipeline.updater import Updater

FM = "---\nmarkmap:\n  colorFreezeLevel: 2\n---\n"
BODY = "# Root\n\n## Topic A\n\n- point 1\n\n### Sub A1\n\n- detail\n\n## Topic B\n\n- point 2\n"


class FakeBackend:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


class TestMarkdownHelpers:
    def test_headings_returns_heading_lines_in_order(self):
        assert headings(BODY) == ["# Root", "## Topic A", "### Sub A1", "## Topic B"]

    def test_headings_ignores_fenced_code_blocks(self):
        md = "# Root\n\n```\n# not a heading\n```\n\n## Real\n"
        assert headings(md) == ["# Root", "## Real"]

    def test_headings_normalizes_trailing_whitespace(self):
        assert headings("#  Root  \n") == ["# Root"]

    def test_root_count(self):
        assert root_count(BODY) == 1
        assert root_count("## only sub\n") == 0
        assert root_count("# A\n\n# B\n") == 2
        assert root_count("# A\n\n```\n# not heading\n```\n") == 1

    def test_split_and_join_frontmatter(self):
        fm, body = split_frontmatter(FM + BODY)
        assert fm == FM
        assert body == BODY
        assert join_frontmatter(fm, body) == FM + BODY

    def test_split_frontmatter_without_frontmatter(self):
        assert split_frontmatter(BODY) == ("", BODY)

    def test_clean_model_output_strips_code_fence(self):
        assert clean_model_output("```markdown\n# Root\n\n## A\n```\n") == "# Root\n\n## A\n"
        assert clean_model_output("```\n# Root\n```") == "# Root\n"

    def test_clean_model_output_drops_preamble_before_first_heading(self):
        assert clean_model_output("以下が更新後のマインドマップです。\n\n# Root\n\n## A\n") == "# Root\n\n## A\n"

    def test_clean_model_output_ensures_trailing_newline(self):
        assert clean_model_output("# Root").endswith("\n")

    def test_fallback_append_adds_unsorted_section_with_turns(self):
        turns = [Turn("user", "質問です"), Turn("assistant", "回答\n複数行")]
        out = fallback_append(BODY, turns)
        assert out.startswith(BODY.rstrip("\n"))
        assert "## Unsorted" in out
        assert "- user: 質問です" in out
        assert "- assistant: 回答 複数行" in out

    def test_fallback_append_reuses_existing_unsorted_section(self):
        once = fallback_append(BODY, [Turn("user", "a")])
        twice = fallback_append(once, [Turn("user", "b")])
        assert twice.count("## Unsorted") == 1
        assert "- user: a" in twice and "- user: b" in twice

    def test_initial_document_has_frontmatter_and_root(self):
        doc = initial_document("worker")
        fm, body = split_frontmatter(doc)
        assert fm.startswith("---\nmarkmap:")
        assert body.startswith("# worker")


class TestSkeletonAndFactory:
    def test_is_skeleton(self):
        assert is_skeleton("")
        assert is_skeleton(initial_document("x"))
        assert is_skeleton(FM + "# only root\n")
        assert not is_skeleton(FM + BODY)

    def test_make_backend_selects_by_name(self, tmp_path):
        assert isinstance(make_backend("claude-cli", model="m", scratch_dir=str(tmp_path)), ClaudeCliBackend)
        assert make_backend("claude-cli", model="m").model == "m"
        assert make_backend("claude-cli", model="m", effort="medium").effort == "medium"
        anthropic = make_backend("anthropic", model="m", effort="low")
        assert isinstance(anthropic, AnthropicBackend) and anthropic.effort == "low"


class TestAtomicWrite:
    def test_writes_content_and_leaves_no_temp_files(self, tmp_path):
        target = tmp_path / "out.md"
        atomic_write(target, "hello\n")
        assert target.read_text(encoding="utf-8") == "hello\n"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["out.md"]

    def test_overwrites_existing_file(self, tmp_path):
        target = tmp_path / "out.md"
        target.write_text("old")
        atomic_write(target, "new")
        assert target.read_text() == "new"


class TestClaudeCliBackend:
    def test_builds_non_interfering_command(self, tmp_path):
        calls = []

        def run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="# Root\n", stderr="")

        backend = ClaudeCliBackend(model="claude-opus-5", scratch_dir=tmp_path, runner=run)
        assert backend.complete("SYS", "USER") == "# Root\n"
        cmd, kwargs = calls[0]
        assert cmd[:2] == ["claude", "-p"]
        assert "--bare" not in cmd  # --bare skips the keychain and therefore cannot find the login
        assert "--no-session-persistence" in cmd
        assert cmd[cmd.index("--setting-sources") + 1] == ""  # no hooks or settings are loaded
        assert cmd[cmd.index("--tools") + 1] == ""
        assert cmd[cmd.index("--output-format") + 1] == "text"
        assert cmd[cmd.index("--system-prompt") + 1] == "SYS"
        assert cmd[cmd.index("--model") + 1] == "claude-opus-5"
        assert kwargs["input"] == "USER"
        assert Path(kwargs["cwd"]) == tmp_path

    def test_effort_flag_is_passed_when_set(self, tmp_path):
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="# R\n", stderr="")

        ClaudeCliBackend(model="claude-opus-5", scratch_dir=tmp_path, runner=run, effort="medium").complete("S", "U")
        assert calls[0][calls[0].index("--effort") + 1] == "medium"
        ClaudeCliBackend(model="claude-opus-5", scratch_dir=tmp_path, runner=run).complete("S", "U")
        assert "--effort" not in calls[1]

    def test_omits_model_flag_when_model_is_none(self, tmp_path):
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="# R\n", stderr="")

        ClaudeCliBackend(model=None, scratch_dir=tmp_path, runner=run).complete("S", "U")
        assert "--model" not in calls[0]

    @pytest.mark.parametrize("stdout,stderr", [("", "Not logged in"), ("Not logged in · Please run /login", "")])
    def test_nonzero_exit_raises_backend_error_with_output(self, tmp_path, stdout, stderr):
        def run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout=stdout, stderr=stderr)

        with pytest.raises(BackendError) as exc:
            ClaudeCliBackend(model=None, scratch_dir=tmp_path, runner=run).complete("S", "U")
        assert "Not logged in" in str(exc.value)

    def test_timeout_and_missing_binary_raise_backend_error(self, tmp_path):
        def timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 1)

        def missing(cmd, **kwargs):
            raise FileNotFoundError("claude")

        with pytest.raises(BackendError):
            ClaudeCliBackend(model=None, scratch_dir=tmp_path, runner=timeout).complete("S", "U")
        with pytest.raises(BackendError):
            ClaudeCliBackend(model=None, scratch_dir=tmp_path, runner=missing).complete("S", "U")

    def test_creates_scratch_dir_if_missing(self, tmp_path):
        scratch = tmp_path / "nested" / "scratch"

        def run(cmd, **kwargs):
            assert Path(kwargs["cwd"]).is_dir()
            return subprocess.CompletedProcess(cmd, 0, stdout="# R\n", stderr="")

        ClaudeCliBackend(model=None, scratch_dir=scratch, runner=run).complete("S", "U")
        assert scratch.is_dir()


class _Block:
    def __init__(self, type_, text=None):
        self.type = type_
        self.text = text


class _Message:
    def __init__(self, blocks, stop_reason="end_turn"):
        self.content = blocks
        self.stop_reason = stop_reason
        self.stop_details = None


class _Stream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeAnthropicClient:
    def __init__(self, message):
        self.message = message
        self.kwargs = None

        outer = self

        class _Messages:
            def stream(self, **kwargs):
                outer.kwargs = kwargs
                return _Stream(outer.message)

        self.messages = _Messages()


class TestAnthropicBackend:
    def test_joins_text_blocks_and_passes_system_and_user(self):
        client = FakeAnthropicClient(_Message([_Block("thinking"), _Block("text", "# Root\n"), _Block("text", "## A\n")]))
        backend = AnthropicBackend(model="claude-opus-5", client=client)
        assert backend.complete("SYS", "USER") == "# Root\n## A\n"
        assert client.kwargs["model"] == "claude-opus-5"
        assert client.kwargs["system"] == "SYS"
        assert client.kwargs["messages"] == [{"role": "user", "content": "USER"}]
        assert client.kwargs["max_tokens"] >= 16000

    def test_effort_is_passed_via_output_config_when_given(self):
        client = FakeAnthropicClient(_Message([_Block("text", "# R\n")]))
        AnthropicBackend(model="claude-opus-5", client=client, effort="low").complete("S", "U")
        assert client.kwargs["output_config"] == {"effort": "low"}

    def test_refusal_raises_backend_error(self):
        client = FakeAnthropicClient(_Message([], stop_reason="refusal"))
        with pytest.raises(BackendError) as exc:
            AnthropicBackend(model="claude-opus-5", client=client).complete("S", "U")
        assert "refusal" in str(exc.value)

    def test_max_tokens_stop_raises_backend_error(self):
        client = FakeAnthropicClient(_Message([_Block("text", "partial")], stop_reason="max_tokens"))
        with pytest.raises(BackendError):
            AnthropicBackend(model="claude-opus-5", client=client).complete("S", "U")


class TestUpdater:
    def make(self, tmp_path, outputs, **kwargs):
        backend = FakeBackend(outputs)
        updater = Updater(backend, tmp_path / "mindmap_worker.md", agent_name="worker", **kwargs)
        return backend, updater

    def test_no_turns_is_noop_without_backend_call(self, tmp_path):
        backend, updater = self.make(tmp_path, [])
        result = updater.update([])
        assert result.status == "noop"
        assert backend.calls == []
        assert not (tmp_path / "mindmap_worker.md").exists()

    def test_initial_creation_uses_initial_prompt_and_adds_frontmatter(self, tmp_path):
        backend, updater = self.make(tmp_path, ["# worker\n\n## Topic\n\n- item\n"])
        result = updater.update([Turn("user", "hello")])
        assert result.status == "created"
        system, user = backend.calls[0]
        assert "worker" in user and "hello" in user
        content = (tmp_path / "mindmap_worker.md").read_text(encoding="utf-8")
        fm, body = split_frontmatter(content)
        assert fm.startswith("---\nmarkmap:")
        assert body == "# worker\n\n## Topic\n\n- item\n"

    def test_initial_output_without_heading_falls_back_to_skeleton(self, tmp_path):
        backend, updater = self.make(tmp_path, ["just prose, no headings"])
        result = updater.update([Turn("user", "hello")])
        assert result.status == "fallback"
        content = (tmp_path / "mindmap_worker.md").read_text(encoding="utf-8")
        assert "# worker" in content
        assert "## Unsorted" in content and "- user: hello" in content

    def test_incremental_update_preserves_frontmatter_and_applies_the_patch(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        out.write_text(FM + BODY, encoding="utf-8")
        backend, updater = self.make(tmp_path, ['{"additions": [{"new_section": "## Topic C", "items": ["- c"]}]}'])
        result = updater.update([Turn("user", "c please")])
        assert result.status == "updated"
        assert result.attempts == 1
        system, user = backend.calls[0]
        assert "## Topic A" in user and "c please" in user
        assert "colorFreezeLevel" not in user  # frontmatter is not sent to the model
        assert out.read_text(encoding="utf-8") == FM + BODY + "\n## Topic C\n\n- c\n"

    def test_initial_output_with_two_roots_falls_back_to_skeleton(self, tmp_path):
        backend, updater = self.make(tmp_path, ["# A\n\n## x\n\n# B\n\n## y\n"])
        result = updater.update([Turn("user", "hello")])
        assert result.status == "fallback"
        content = (tmp_path / "mindmap_worker.md").read_text(encoding="utf-8")
        assert content.count("\n# ") + content.startswith("# ") <= 1 or root_count(content) == 1

    def test_persistent_violation_falls_back_to_append(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        out.write_text(FM + BODY, encoding="utf-8")
        broken = "# Root\n\n## Only A\n"
        backend, updater = self.make(tmp_path, [broken, broken], max_retries=1)
        result = updater.update([Turn("user", "kept?"), Turn("assistant", "yes")])
        assert result.status == "fallback"
        content = out.read_text(encoding="utf-8")
        assert content.startswith(FM + BODY.rstrip("\n"))
        assert "## Unsorted" in content
        assert "- user: kept?" in content
        assert len(backend.calls) == 2

    def test_backend_error_propagates_and_leaves_file_untouched(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        out.write_text(FM + BODY, encoding="utf-8")
        backend, updater = self.make(tmp_path, [BackendError("boom")], instant_placeholder=False)
        with pytest.raises(BackendError):
            updater.update([Turn("user", "x")])
        assert out.read_text(encoding="utf-8") == FM + BODY

    def test_empty_existing_file_is_treated_as_initial(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        out.write_text("", encoding="utf-8")
        backend, updater = self.make(tmp_path, ["# worker\n\n## T\n"])
        assert updater.update([Turn("user", "x")]).status == "created"

    def test_skeleton_with_only_root_heading_is_treated_as_initial(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        out.write_text(initial_document("worker"), encoding="utf-8")
        backend, updater = self.make(tmp_path, ["# worker\n\n## T\n\n- x\n"])
        result = updater.update([Turn("user", "x")])
        assert result.status == "created"
        system, user = backend.calls[0]
        assert "<current_mindmap>" not in user
        assert split_frontmatter(out.read_text(encoding="utf-8"))[1] == "# worker\n\n## T\n\n- x\n"


class PatchBackend:
    """Returns scripted outputs and records what the output file looked like when each call was made."""

    def __init__(self, outputs, output_path=None):
        self.outputs = list(outputs)
        self.calls = []
        self.files_at_call = []
        self.output_path = output_path

    def complete(self, system, user):
        self.calls.append((system, user))
        if self.output_path is not None:
            self.files_at_call.append(self.output_path.read_text(encoding="utf-8") if self.output_path.exists() else None)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


class TestPatchMerge:
    def make(self, tmp_path, outputs, **kwargs):
        out = tmp_path / "mindmap_worker.md"
        backend = PatchBackend(outputs, output_path=out)
        return backend, out, Updater(backend, out, agent_name="worker", **kwargs)

    def test_patch_is_applied_instead_of_rewriting_the_map(self, tmp_path):
        backend, out, updater = self.make(tmp_path, ['{"additions": [{"under": "## Topic A", "items": ["- from patch"]}]}'])
        out.write_text(FM + BODY, encoding="utf-8")
        result = updater.update([Turn("user", "x")])
        assert result.status == "updated" and result.attempts == 1
        assert out.read_text(encoding="utf-8") == FM + BODY.replace("- point 1\n", "- point 1\n- from patch\n", 1)
        assert "<headings>" in backend.calls[0][1]

    def test_root_retitle_via_patch(self, tmp_path):
        backend, out, updater = self.make(tmp_path, ['{"root_title": "Topic of the talk", "additions": []}'])
        out.write_text(FM + BODY, encoding="utf-8")
        assert updater.update([Turn("user", "x")]).status == "updated"
        assert headings(out.read_text(encoding="utf-8"))[0] == "# Topic of the talk"

    def test_invalid_json_is_retried_then_falls_back(self, tmp_path):
        backend, out, updater = self.make(tmp_path, ["not json", "still not json"], max_retries=1)
        out.write_text(FM + BODY, encoding="utf-8")
        result = updater.update([Turn("user", "kept?")])
        assert result.status == "fallback" and result.attempts == 2
        assert "JSON" in backend.calls[1][1]
        content = out.read_text(encoding="utf-8")
        assert content.startswith(FM + BODY.rstrip("\n")) and "- user: kept?" in content

    def test_unknown_heading_is_retried_then_unresolved_items_go_to_unsorted(self, tmp_path):
        bad = '{"additions": [{"under": "## Nope", "items": ["- lost"]}]}'
        backend, out, updater = self.make(tmp_path, [bad, bad], max_retries=1)
        out.write_text(FM + BODY, encoding="utf-8")
        result = updater.update([Turn("user", "q")])
        assert result.status == "updated" and result.attempts == 2
        assert "## Nope" in backend.calls[1][1]
        content = out.read_text(encoding="utf-8")
        assert "## Unsorted" in content and "- lost" in content
        assert [h for h in headings(content) if h in headings(BODY)] == headings(BODY)

    def test_empty_patch_is_a_valid_noop_update(self, tmp_path):
        backend, out, updater = self.make(tmp_path, ['{"additions": []}'])
        out.write_text(FM + BODY, encoding="utf-8")
        assert updater.update([Turn("user", "nothing new")]).status == "updated"
        assert out.read_text(encoding="utf-8") == FM + BODY


class TestMergeBackend:
    def test_initial_build_uses_the_main_backend_and_merges_use_the_merge_backend(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        main = PatchBackend(["# worker\n\n## Topic A\n\n- a\n"], output_path=out)
        merge = PatchBackend(['{"additions": [{"under": "## Topic A", "items": ["- b"]}]}'], output_path=out)
        updater = Updater(main, out, agent_name="worker", merge_backend=merge)
        assert updater.update([Turn("user", "first")]).status == "created"
        assert updater.update([Turn("user", "second")]).status == "updated"
        assert len(main.calls) == 1 and len(merge.calls) == 1
        assert "- b" in out.read_text(encoding="utf-8")

    def test_without_a_merge_backend_the_main_backend_does_both(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        main = PatchBackend(["# worker\n\n## Topic A\n\n- a\n", '{"additions": []}'], output_path=out)
        updater = Updater(main, out, agent_name="worker")
        updater.update([Turn("user", "first")])
        updater.update([Turn("user", "second")])
        assert len(main.calls) == 2


class TestTwoPhaseUpdate:
    def test_pending_section_is_written_before_the_model_is_called_and_removed_after(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        backend = PatchBackend(['{"additions": [{"under": "## Topic A", "items": ["- merged"]}]}'], output_path=out)
        out.write_text(FM + BODY, encoding="utf-8")
        Updater(backend, out, agent_name="worker").update([Turn("user", "instant question")])
        assert "- user: instant question" in backend.files_at_call[0]  # visible while the model works
        final = out.read_text(encoding="utf-8")
        assert "instant question" not in final and "- merged" in final
        assert "<current_mindmap>\n" + BODY.rstrip("\n") in backend.calls[0][1]  # the model never sees the placeholder

    def test_pending_section_is_shown_during_the_initial_build_too(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        backend = PatchBackend(["# worker\n\n## T\n\n- t\n"], output_path=out)
        Updater(backend, out, agent_name="worker").update([Turn("user", "first question")])
        assert "- user: first question" in backend.files_at_call[0]
        assert "first question" not in out.read_text(encoding="utf-8")

    def test_pending_left_by_a_failed_call_is_stripped_on_the_next_run(self, tmp_path):
        from markmap_pipeline.backends import BackendError

        out = tmp_path / "mindmap_worker.md"
        backend = PatchBackend([BackendError("boom"), '{"additions": []}'], output_path=out)
        out.write_text(FM + BODY, encoding="utf-8")
        updater = Updater(backend, out, agent_name="worker")
        with pytest.raises(BackendError):
            updater.update([Turn("user", "q1")])
        assert "- user: q1" in out.read_text(encoding="utf-8")  # placeholder stays visible after a failure
        updater.update([Turn("user", "q1"), Turn("user", "q2")])
        assert "<current_mindmap>\n" + BODY.rstrip("\n") in backend.calls[1][1]
        assert out.read_text(encoding="utf-8") == FM + BODY

    def test_placeholder_can_be_disabled(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        backend = PatchBackend(['{"additions": []}'], output_path=out)
        out.write_text(FM + BODY, encoding="utf-8")
        Updater(backend, out, agent_name="worker", instant_placeholder=False).update([Turn("user", "q")])
        assert backend.files_at_call[0] == FM + BODY


class TestMapLanguage:
    def make(self, tmp_path, outputs, **kwargs):
        backend = FakeBackend(outputs)
        return backend, Updater(backend, tmp_path / "mindmap_worker.md", agent_name="worker", **kwargs)

    def test_system_prompt_leaves_the_language_to_the_model(self, tmp_path):
        backend, updater = self.make(tmp_path, ["# Root\n\n## T\n\n- t\n"])
        updater.update([Turn("user", "Please design the plugin architecture"), Turn("assistant", "了解しました。")])
        system, _ = backend.calls[0]
        assert "Language" in system and "[user]" in system
        assert "in Japanese" not in system and "in English." not in system

    def test_section_labels_are_english_regardless_of_the_conversation(self, tmp_path):
        backend, updater = self.make(tmp_path, ["just prose, no headings"])
        updater.update([Turn("user", "プラグインの構成を設計してください"), Turn("assistant", "了解しました。")])
        content = (tmp_path / "mindmap_worker.md").read_text(encoding="utf-8")
        assert "## Unsorted" in content and "未整理" not in content

    def test_pending_section_label_is_english(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        backend = PatchBackend(['{"additions": []}'], output_path=out)
        out.write_text(FM + BODY, encoding="utf-8")
        Updater(backend, out, agent_name="worker").update([Turn("user", "続けてください")])
        assert "## Latest (pending merge)" in backend.files_at_call[0]
        assert "最新" not in backend.files_at_call[0]


class TestLanguageDetectionWiring:
    def test_detected_language_is_stated_in_the_initial_system_prompt(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        lang = FakeBackend(["English"])
        main = PatchBackend(["# Root\n\n## T\n\n- t\n"], output_path=out)
        result = Updater(main, out, agent_name="worker", language_backend=lang).update([Turn("user", "What is herdr?"), Turn("assistant", "はい")])
        assert result.status == "created"
        assert "What is herdr?" in lang.calls[0][1] and "はい" not in lang.calls[0][1]  # only the [user] lines are judged
        assert "written in English. Write the entire mind map in English" in main.calls[0][0]

    def test_detection_sees_the_existing_headings_and_applies_to_merges(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        out.write_text(FM + BODY, encoding="utf-8")
        lang = FakeBackend(["Japanese"])
        merge = PatchBackend(['{"additions": []}'], output_path=out)
        Updater(merge, out, agent_name="worker", language_backend=lang).update([Turn("user", "ok")])
        assert "## Topic A" in lang.calls[0][1]
        assert "written in Japanese" in merge.calls[0][0]

    def test_unusable_detection_answer_falls_back_to_the_rule_only_prompt(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        lang = FakeBackend(["unknown"])
        main = PatchBackend(["# Root\n\n## T\n\n- t\n"], output_path=out)
        Updater(main, out, agent_name="worker", language_backend=lang).update([Turn("user", "ok")])
        assert main.calls[0][0] == INITIAL_SYSTEM

    def test_detection_failure_does_not_block_the_merge(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        lang = FakeBackend([BackendError("boom")])
        main = PatchBackend(["# Root\n\n## T\n\n- t\n"], output_path=out)
        result = Updater(main, out, agent_name="worker", language_backend=lang).update([Turn("user", "What is herdr?")])
        assert result.status == "created"
        assert main.calls[0][0] == INITIAL_SYSTEM

    def test_without_a_language_backend_no_detection_call_is_made(self, tmp_path):
        out = tmp_path / "mindmap_worker.md"
        main = PatchBackend(["# Root\n\n## T\n\n- t\n"], output_path=out)
        Updater(main, out, agent_name="worker").update([Turn("user", "What is herdr?")])
        assert len(main.calls) == 1 and main.calls[0][0] == INITIAL_SYSTEM
