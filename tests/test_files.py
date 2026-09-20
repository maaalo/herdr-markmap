"""Atomic writes, JSON persistence and safe file names shared by the registry, the state and the config."""
import json

import pytest

from markmap_pipeline.files import atomic_write, read_json, safe_name, write_json


class TestAtomicWrite:
    def test_creates_missing_parent_directories(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "map.md"
        atomic_write(path, "# Root\n")
        assert path.read_text(encoding="utf-8") == "# Root\n"

    def test_replaces_an_existing_file_and_leaves_no_temporary_behind(self, tmp_path):
        path = tmp_path / "map.md"
        atomic_write(path, "old")
        atomic_write(path, "new")
        assert path.read_text(encoding="utf-8") == "new"
        assert [p.name for p in tmp_path.iterdir()] == ["map.md"]


class TestJson:
    def test_round_trips_an_object(self, tmp_path):
        path = tmp_path / "state.json"
        write_json(path, {"offset": 12, "label": "作業"})
        assert read_json(path) == {"offset": 12, "label": "作業"}

    def test_is_written_unescaped_and_indented_so_it_can_be_edited_by_hand(self, tmp_path):
        path = tmp_path / "config.json"
        write_json(path, {"label": "作業"})
        text = path.read_text(encoding="utf-8")
        assert "作業" in text and text.startswith("{\n  ") and text.endswith("\n")

    @pytest.mark.parametrize("content", ["not json", "[1, 2]", '"text"', ""])
    def test_returns_none_when_the_file_is_not_a_json_object(self, tmp_path, content):
        path = tmp_path / "broken.json"
        path.write_text(content, encoding="utf-8")
        assert read_json(path) is None

    def test_returns_none_when_the_file_is_missing(self, tmp_path):
        assert read_json(tmp_path / "absent.json") is None

    def test_returns_none_for_a_directory_instead_of_raising(self, tmp_path):
        assert read_json(tmp_path) is None


class TestSafeName:
    @pytest.mark.parametrize(
        "value,expected",
        [("pane-1", "pane-1"), ("%1", "_1"), ("a/b c", "a_b_c"), ("作業", "__")],
    )
    def test_keeps_only_characters_that_are_safe_in_a_file_name(self, value, expected):
        assert safe_name(value) == expected
