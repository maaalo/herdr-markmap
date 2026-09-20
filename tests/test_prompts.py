"""The prompts are English so the model is not steered towards any one map language; a language rule in
each system prompt tells it to write in the language of the user's turns."""
import re

from markmap_pipeline.extract import Turn
from markmap_pipeline.prompts import (
    INCREMENTAL_SYSTEM,
    INITIAL_SYSTEM,
    build_incremental_user,
    build_initial_user,
    build_retry_note,
    render_turns,
)

CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿＀-￯]")


class TestSystemPrompts:
    def test_incremental_prompt_forbids_destroying_structure(self):
        for keyword in ("heading", "delete", "rename", "reorder", "Markdown"):
            assert keyword in INCREMENTAL_SYSTEM

    def test_initial_prompt_requires_single_root(self):
        assert "# " in INITIAL_SYSTEM
        assert "exactly one root" in INITIAL_SYSTEM

    def test_initial_prompt_makes_root_a_topic_title_not_a_directory_name(self):
        assert "topic" in INITIAL_SYSTEM
        for forbidden in ("directory name", "agent name"):
            assert forbidden in INITIAL_SYSTEM  # explicitly forbidden as root titles

    def test_incremental_prompt_allows_retitling_root_but_protects_other_headings(self):
        assert "root_title" in INCREMENTAL_SYSTEM
        assert "retitle" in INCREMENTAL_SYSTEM or "retitled" in INCREMENTAL_SYSTEM
        assert "no other heading" in INCREMENTAL_SYSTEM or "other headings cannot" in INCREMENTAL_SYSTEM

    def test_prompts_forbid_code_fences_and_commentary(self):
        for prompt in (INITIAL_SYSTEM, INCREMENTAL_SYSTEM):
            assert "```" in prompt
            assert "preamble" in prompt or "commentary" in prompt


class TestRenderTurns:
    def test_renders_role_and_text(self):
        text = render_turns([Turn("user", "質問", "2026-09-18T05:57:04Z"), Turn("assistant", "回答")])
        assert "[user 2026-09-18T05:57:04Z]" in text
        assert "質問" in text
        assert "[assistant]" in text
        assert "回答" in text

    def test_long_turn_is_truncated_with_marker(self):
        text = render_turns([Turn("assistant", "x" * 100)], max_turn_chars=10)
        assert "x" * 10 in text
        assert "x" * 11 not in text
        assert "omitted" in text

    def test_total_budget_keeps_newest_turns(self):
        turns = [Turn("user", "old" * 10), Turn("user", "mid" * 10), Turn("user", "new" * 10)]
        text = render_turns(turns, max_total_chars=80)
        assert "newnew" in text
        assert "oldold" not in text
        assert "omitted" in text


class TestUserMessages:
    def test_initial_user_includes_agent_and_turns(self):
        text = build_initial_user("worker", "[user] hello")
        assert "worker" in text
        assert "[user] hello" in text
        assert "context only" in text or "for reference" in text  # the workspace name is context only

    def test_incremental_user_includes_current_map_and_turns(self):
        text = build_incremental_user("# Root\n\n## A\n- x", "[user] hello")
        assert "# Root" in text
        assert "## A" in text
        assert "[user] hello" in text
        assert text.index("# Root") < text.index("[user] hello")

    def test_retry_note_lists_missing_headings(self):
        note = build_retry_note(["## A", "### B"])
        assert "## A" in note and "### B" in note


class TestPatchPrompt:
    def test_incremental_prompt_asks_for_json_patch(self):
        for needle in ("JSON", "additions", "under", "new_section", "root_title", "items"):
            assert needle in INCREMENTAL_SYSTEM, needle

    def test_incremental_user_lists_existing_headings_for_exact_matching(self):
        text = build_incremental_user("# Root\n\n## A\n\n- x\n\n### A1\n", "[user] hello")
        assert "<headings>" in text and "## A" in text and "### A1" in text
        assert "<current_mindmap>" in text and "[user] hello" in text

    def test_retry_note_for_patch_problems(self):
        note = build_retry_note(missing=[], patch_problem="Output was not valid JSON")
        assert "JSON" in note
        note = build_retry_note(missing=["## Nope"])
        assert "## Nope" in note


class TestLanguageRule:
    def test_prompts_tell_the_model_to_write_in_the_language_of_the_user_turns(self):
        for prompt in (INITIAL_SYSTEM, INCREMENTAL_SYSTEM):
            assert "Language" in prompt
            assert "text of the [user] lines" in prompt
            assert "mixed" in prompt or "other languages" in prompt  # code, errors, quotes must not change it

    def test_language_rule_scopes_the_user_to_the_log_not_the_account_holder(self):
        # Claude Code adds account context even in -p mode; "the user's language" was read as the account
        # holder's language and gave Japanese maps for English conversations (3/3 in a real run).
        for prompt in (INITIAL_SYSTEM, INCREMENTAL_SYSTEM):
            assert "account" in prompt and "locale" in prompt
            assert "not the [user]" in prompt
            assert "the language the user writes in" not in prompt

    def test_prompts_do_not_pin_the_map_to_a_fixed_language(self):
        for prompt in (INITIAL_SYSTEM, INCREMENTAL_SYSTEM):
            for pinned in ("in Japanese", "in English.", "日本語で書く", "英語で書く"):
                assert pinned not in prompt

    def test_incremental_prompt_keeps_a_short_user_turn_in_the_existing_maps_language(self):
        assert "language of the existing mind map" in INCREMENTAL_SYSTEM

    def test_everything_sent_to_the_model_is_english_except_the_conversation_itself(self):
        # A Japanese prompt steers the model into Japanese regardless of the rule (seen in a real run).
        for text in (
            INITIAL_SYSTEM,
            INCREMENTAL_SYSTEM,
            build_initial_user("worker", "TURNS"),
            build_incremental_user("# Root\n\n## A\n", "TURNS"),
            build_retry_note(["## A"], patch_problem="bad"),
            render_turns([Turn("user", "x" * 50)], max_turn_chars=10),
            render_turns([Turn("user", "a" * 30), Turn("user", "b" * 30)], max_total_chars=40),
        ):
            assert not CJK.search(text), text


class TestLanguageDetection:
    """Real runs showed the rule alone is not enough: with the account context Claude Code adds to -p runs,
    English conversations still came out Japanese 3/3. Asking the model for the language first and then
    stating it explicitly gave English 3/3, so the language is detected in a separate call and injected."""

    def test_detect_prompt_asks_for_a_bare_language_name(self):
        from markmap_pipeline.prompts import DETECT_LANGUAGE_SYSTEM

        assert "name of the language" in DETECT_LANGUAGE_SYSTEM
        assert "unknown" in DETECT_LANGUAGE_SYSTEM
        assert "[user]" in DETECT_LANGUAGE_SYSTEM or "<user_lines>" in DETECT_LANGUAGE_SYSTEM
        assert "account" in DETECT_LANGUAGE_SYSTEM  # the account holder's locale must not leak in

    def test_detect_user_includes_user_lines_and_existing_headings(self):
        from markmap_pipeline.prompts import build_detect_language_user

        text = build_detect_language_user(["What is herdr?"], "# Root\n\n## A\n\n- x\n")
        assert "<user_lines>" in text and "What is herdr?" in text
        assert "<existing_headings>" in text and "## A" in text and "- x" not in text

    def test_detect_user_without_a_map(self):
        from markmap_pipeline.prompts import build_detect_language_user

        text = build_detect_language_user(["hi"], None)
        assert "<user_lines>" in text and "hi" in text
        assert "<existing_headings>" not in text

    def test_parse_language_name_accepts_only_a_bare_name(self):
        from markmap_pipeline.prompts import parse_language_name

        assert parse_language_name("English") == "English"
        assert parse_language_name("  Japanese.\n") == "Japanese"
        assert parse_language_name("Brazilian Portuguese") == "Brazilian Portuguese"
        assert parse_language_name("The language is English") is None
        assert parse_language_name("unknown") is None
        assert parse_language_name("Unknown.") is None
        assert parse_language_name("") is None
        assert parse_language_name("English\nJapanese") is None
        assert parse_language_name("x" * 60) is None

    def test_system_prompts_state_the_detected_language_explicitly(self):
        from markmap_pipeline.prompts import incremental_system, initial_system

        assert "written in English. Write the entire mind map in English" in initial_system("English")
        assert initial_system("English").startswith(INITIAL_SYSTEM)
        assert "written in English" in incremental_system("English") and "additions" in incremental_system("English")
        assert incremental_system("English").startswith(INCREMENTAL_SYSTEM)

    def test_without_a_detected_language_the_base_prompts_are_used(self):
        from markmap_pipeline.prompts import incremental_system, initial_system

        assert initial_system(None) == INITIAL_SYSTEM
        assert incremental_system(None) == INCREMENTAL_SYSTEM

    def test_detection_texts_are_english(self):
        from markmap_pipeline.prompts import DETECT_LANGUAGE_SYSTEM, build_detect_language_user, initial_system

        for text in (DETECT_LANGUAGE_SYSTEM, build_detect_language_user(["x"], "# R\n"), initial_system("French")):
            assert not CJK.search(text), text
