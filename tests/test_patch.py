"""Patch-based merge: the model returns additions as JSON and Python inserts them into the map."""
import json

import pytest

from markmap_pipeline.extract import Turn
from markmap_pipeline.markdown import headings
from markmap_pipeline.patch import (
    PENDING_HEADING,
    UNSORTED_HEADING,
    Patch,
    PatchError,
    append_unsorted,
    apply_patch,
    parse_patch,
    strip_pending,
    with_pending,
)

BODY = "# Root\n\n## Topic A\n\n- point 1\n\n### Sub A1\n\n- detail\n\n## Topic B\n\n- point 2\n"


class TestParsePatch:
    def test_raw_json(self):
        patch = parse_patch('{"root_title": null, "additions": [{"under": "## Topic A", "items": ["- x"]}]}')
        assert patch == Patch(root_title=None, additions=[{"under": "## Topic A", "items": ["- x"]}])

    def test_json_inside_code_fence_with_preamble(self):
        text = 'Here is the patch:\n```json\n{"additions": [{"new_section": "## C", "items": ["- y"]}]}\n```\nDone.'
        assert parse_patch(text).additions == [{"new_section": "## C", "items": ["- y"]}]

    def test_missing_additions_defaults_to_empty(self):
        assert parse_patch('{"root_title": "New"}') == Patch(root_title="New", additions=[])

    @pytest.mark.parametrize("text", ["", "no json here", "{broken", '["not", "an", "object"]', '{"additions": "nope"}'])
    def test_invalid_input_raises(self, text):
        with pytest.raises(PatchError):
            parse_patch(text)

    def test_items_given_as_a_single_string_are_split_into_lines(self):
        patch = parse_patch('{"additions": [{"under": "## Topic A", "items": "- a\\n- b"}]}')
        assert patch.additions[0]["items"] == ["- a", "- b"]


class TestApplyPatch:
    def test_bullets_go_directly_under_the_heading_before_its_first_child(self):
        patch = Patch(additions=[{"under": "## Topic A", "items": ["- point 1b"]}])
        out, unresolved = apply_patch(BODY, patch)
        assert unresolved == []
        assert out == "# Root\n\n## Topic A\n\n- point 1\n- point 1b\n\n### Sub A1\n\n- detail\n\n## Topic B\n\n- point 2\n"

    def test_subheadings_go_to_the_end_of_the_subtree(self):
        patch = Patch(additions=[{"under": "## Topic A", "items": ["### Sub A2", "- new detail"]}])
        out, _ = apply_patch(BODY, patch)
        assert out == "# Root\n\n## Topic A\n\n- point 1\n\n### Sub A1\n\n- detail\n\n### Sub A2\n\n- new detail\n\n## Topic B\n\n- point 2\n"
        assert headings(out) == ["# Root", "## Topic A", "### Sub A1", "### Sub A2", "## Topic B"]

    def test_mixed_items_split_into_direct_bullets_and_subtree(self):
        patch = Patch(additions=[{"under": "## Topic A", "items": ["- direct", "### Sub A2", "- under a2"]}])
        out, _ = apply_patch(BODY, patch)
        assert "- point 1\n- direct\n\n### Sub A1" in out
        assert out.rstrip().endswith("- point 2") is True
        assert "### Sub A2\n\n- under a2\n\n## Topic B" in out

    def test_under_a_third_level_heading(self):
        patch = Patch(additions=[{"under": "### Sub A1", "items": ["- more detail"]}])
        out, _ = apply_patch(BODY, patch)
        assert "### Sub A1\n\n- detail\n- more detail\n\n## Topic B" in out

    def test_under_matches_heading_text_without_hashes_and_with_extra_spaces(self):
        for under in ("Topic A", "##  Topic A ", "## topic a"):
            out, unresolved = apply_patch(BODY, Patch(additions=[{"under": under, "items": ["- x"]}]))
            assert unresolved == [] and "- point 1\n- x\n" in out, under

    def test_new_section_is_appended_before_the_unsorted_section(self):
        body = BODY + "\n" + UNSORTED_HEADING + "\n\n- user: leftover\n"
        out, _ = apply_patch(body, Patch(additions=[{"new_section": "## Topic C", "items": ["- c"]}]))
        assert headings(out) == ["# Root", "## Topic A", "### Sub A1", "## Topic B", "## Topic C", UNSORTED_HEADING]
        assert "## Topic C\n\n- c\n\n" + UNSORTED_HEADING in out

    def test_new_section_without_hashes_gets_level_two(self):
        out, _ = apply_patch(BODY, Patch(additions=[{"new_section": "Topic C", "items": ["- c"]}]))
        assert headings(out)[-1] == "## Topic C"

    def test_headings_of_level_one_or_two_inside_items_are_demoted_to_three(self):
        out, _ = apply_patch(BODY, Patch(additions=[{"under": "## Topic B", "items": ["# Bad root", "## Bad section", "- ok"]}]))
        assert headings(out).count("# Root") == 1 and "## Bad section" not in headings(out)
        assert "### Bad root" in headings(out) and "### Bad section" in headings(out)

    def test_root_title_is_replaced(self):
        out, _ = apply_patch(BODY, Patch(root_title="What this conversation is about"))
        assert headings(out)[0] == "# What this conversation is about"
        assert headings(out)[1:] == headings(BODY)[1:]

    def test_empty_or_hash_only_root_title_is_ignored(self):
        for title in ("", "   ", "# "):
            out, _ = apply_patch(BODY, Patch(root_title=title))
            assert headings(out)[0] == "# Root"

    def test_unknown_heading_is_reported_and_left_out(self):
        patch = Patch(additions=[{"under": "## Nope", "items": ["- lost?"]}, {"under": "## Topic B", "items": ["- kept"]}])
        out, unresolved = apply_patch(BODY, patch)
        assert unresolved == [{"under": "## Nope", "items": ["- lost?"]}]
        assert "- kept" in out and "- lost?" not in out

    def test_duplicate_bullets_already_present_are_skipped(self):
        out, _ = apply_patch(BODY, Patch(additions=[{"under": "## Topic A", "items": ["- point 1", "- point 1c"]}]))
        assert out.count("- point 1\n") == 1 and "- point 1c" in out

    def test_blank_items_and_empty_additions_are_ignored(self):
        out, unresolved = apply_patch(BODY, Patch(additions=[{"under": "## Topic A", "items": ["", "   "]}, {"items": ["- orphan"]}]))
        assert out == BODY and unresolved == [{"items": ["- orphan"]}]

    def test_structure_below_root_is_always_preserved(self):
        patch = Patch(root_title="Retitled", additions=[{"under": "## Topic B", "items": ["### B1", "- b"]}, {"new_section": "## D", "items": ["- d"]}])
        out, _ = apply_patch(BODY, patch)
        assert [h for h in headings(BODY) if not h.startswith("# ")] == [h for h in headings(out) if h in headings(BODY)]


class TestPending:
    def test_with_pending_adds_a_section_with_turn_summaries(self):
        out = with_pending(BODY, [Turn("user", "First line of a question\nsecond line"), Turn("assistant", "x" * 300)])
        assert headings(out)[-1] == PENDING_HEADING
        assert "- user: First line of a question" in out
        assert "- assistant: " + "x" * 120 + "…" in out

    def test_with_pending_replaces_an_existing_pending_section(self):
        once = with_pending(BODY, [Turn("user", "a")])
        twice = with_pending(once, [Turn("user", "b")])
        assert twice.count(PENDING_HEADING) == 1
        assert "- user: a" not in twice and "- user: b" in twice

    def test_strip_pending_restores_the_original_body(self):
        assert strip_pending(with_pending(BODY, [Turn("user", "a")])) == BODY
        assert strip_pending(BODY) == BODY

    def test_pending_section_sits_before_unsorted(self):
        body = BODY + "\n" + UNSORTED_HEADING + "\n\n- user: leftover\n"
        out = with_pending(body, [Turn("user", "a")])
        assert headings(out)[-2:] == [PENDING_HEADING, UNSORTED_HEADING]
        assert strip_pending(out) == body


class TestSectionLabels:
    def test_labels_are_english(self):
        assert UNSORTED_HEADING == "## Unsorted"
        assert PENDING_HEADING == "## Latest (pending merge)"

    def test_strip_pending_also_removes_the_legacy_japanese_pending_section(self):
        body = BODY + "\n## 最新（統合待ち）\n\n- user: a\n"
        assert strip_pending(body) == BODY

    def test_with_pending_replaces_a_legacy_japanese_pending_section(self):
        body = BODY + "\n## 最新（統合待ち）\n\n- user: a\n"
        out = with_pending(body, [Turn("user", "b")])
        assert headings(out)[-1] == PENDING_HEADING
        assert "最新" not in out and "- user: a" not in out

    def test_append_unsorted_reuses_a_legacy_japanese_unsorted_section(self):
        body = BODY + "\n## 未整理\n\n- old\n"
        out = append_unsorted(body, ["- lost"])
        assert "## Unsorted" not in out
        assert out.count("## 未整理") == 1 and "- old" in out and "- lost" in out

    def test_new_sections_stay_before_a_legacy_unsorted_section(self):
        body = BODY + "\n## 未整理\n\n- leftover\n"
        out, _ = apply_patch(body, Patch(additions=[{"new_section": "## Topic C", "items": ["- c"]}]))
        assert headings(out)[-2:] == ["## Topic C", "## 未整理"]

    def test_pending_sits_before_a_legacy_unsorted_section(self):
        body = BODY + "\n## 未整理\n\n- leftover\n"
        out = with_pending(body, [Turn("user", "a")])
        assert headings(out)[-2:] == [PENDING_HEADING, "## 未整理"]
