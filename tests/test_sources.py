import json
import os
import time
from pathlib import Path

from markmap_pipeline.sources import (
    ClaudeJsonlSource,
    SourceState,
    find_session_file,
    jsonl_cwd,
    project_dir_candidates,
    project_slug,
)


def write_session(directory: Path, name: str, cwd: str, texts, mtime=None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.jsonl"
    lines = [json.dumps({"type": "mode", "mode": "normal"})]
    for text in texts:
        lines.append(json.dumps({"type": "user", "message": {"content": text}, "cwd": cwd, "sessionId": name}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class TestProjectDirCandidates:
    def test_existing_slug_dir_comes_first(self, tmp_path):
        slug_dir = tmp_path / project_slug("/tmp/proj")
        slug_dir.mkdir()
        (tmp_path / "-other").mkdir()
        assert project_dir_candidates("/tmp/proj", tmp_path) == ([slug_dir], True)

    def test_without_slug_dir_all_project_dirs_are_candidates(self, tmp_path):
        (tmp_path / "-a").mkdir()
        (tmp_path / "-b").mkdir()
        (tmp_path / "file.txt").write_text("x")
        candidates, exact = project_dir_candidates("/tmp/proj", tmp_path)
        assert sorted(candidates) == [tmp_path / "-a", tmp_path / "-b"] and exact is False

    def test_missing_projects_root_gives_empty_list(self, tmp_path):
        assert project_dir_candidates("/tmp/proj", tmp_path / "nope") == ([], False)


class TestJsonlCwd:
    def test_returns_first_cwd_found(self, tmp_path):
        path = write_session(tmp_path, "s", "/tmp/proj", ["hi"])
        assert jsonl_cwd(path) == "/tmp/proj"

    def test_returns_none_when_no_cwd(self, tmp_path):
        path = tmp_path / "s.jsonl"
        path.write_text('{"type":"mode"}\nnot json\n', encoding="utf-8")
        assert jsonl_cwd(path) is None


class TestFindSessionFile:
    def test_picks_newest_jsonl_in_slug_dir(self, tmp_path):
        slug_dir = tmp_path / project_slug("/tmp/proj")
        now = time.time()
        old = write_session(slug_dir, "old", "/tmp/proj", ["a"], mtime=now - 100)
        new = write_session(slug_dir, "new", "/tmp/proj", ["b"], mtime=now)
        assert find_session_file("/tmp/proj", tmp_path) == new

    def test_explicit_session_id_wins_over_mtime(self, tmp_path):
        slug_dir = tmp_path / project_slug("/tmp/proj")
        now = time.time()
        old = write_session(slug_dir, "old", "/tmp/proj", ["a"], mtime=now - 100)
        write_session(slug_dir, "new", "/tmp/proj", ["b"], mtime=now)
        assert find_session_file("/tmp/proj", tmp_path, session_id="old") == old

    def test_falls_back_to_scanning_by_cwd_when_slug_mismatches(self, tmp_path):
        weird_dir = tmp_path / "-weird-slug----"
        now = time.time()
        write_session(weird_dir, "other", "/tmp/other", ["x"], mtime=now)
        target = write_session(weird_dir, "mine", "/tmp/日本語 proj", ["y"], mtime=now - 10)
        assert find_session_file("/tmp/日本語 proj", tmp_path) == target

    def test_ignores_non_jsonl_and_subdirectories(self, tmp_path):
        slug_dir = tmp_path / project_slug("/tmp/proj")
        target = write_session(slug_dir, "s", "/tmp/proj", ["a"])
        (slug_dir / "memory").mkdir()
        (slug_dir / "notes.md").write_text("x")
        assert find_session_file("/tmp/proj", tmp_path) == target

    def test_returns_none_when_nothing_matches(self, tmp_path):
        (tmp_path / "-x").mkdir()
        write_session(tmp_path / "-x", "s", "/tmp/other", ["a"])
        assert find_session_file("/tmp/proj", tmp_path) is None


class TestClaudeJsonlSource:
    def test_poll_returns_new_turns_and_advances_state(self, tmp_path):
        slug_dir = tmp_path / project_slug("/tmp/proj")
        path = write_session(slug_dir, "s", "/tmp/proj", ["one", "two"])
        src = ClaudeJsonlSource("/tmp/proj", projects_root=tmp_path)

        turns, state = src.poll(SourceState())
        assert [t.text for t in turns] == ["one", "two"]
        assert state.path == str(path)
        assert state.offset == path.stat().st_size

        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "three"}, "cwd": "/tmp/proj"}) + "\n")
        turns, state = src.poll(state)
        assert [t.text for t in turns] == ["three"]

    def test_poll_switches_to_newer_session_file_and_resets_offset(self, tmp_path):
        slug_dir = tmp_path / project_slug("/tmp/proj")
        now = time.time()
        first = write_session(slug_dir, "first", "/tmp/proj", ["a"], mtime=now - 100)
        src = ClaudeJsonlSource("/tmp/proj", projects_root=tmp_path)
        turns, state = src.poll(SourceState())
        assert state.path == str(first)

        second = write_session(slug_dir, "second", "/tmp/proj", ["b"], mtime=now)
        turns, state = src.poll(state)
        assert [t.text for t in turns] == ["b"]
        assert state.path == str(second)
        assert state.offset == second.stat().st_size

    def test_poll_without_session_file_returns_no_turns(self, tmp_path):
        src = ClaudeJsonlSource("/tmp/proj", projects_root=tmp_path)
        turns, state = src.poll(SourceState())
        assert turns == []
        assert state == SourceState()

    def test_state_roundtrips_through_dict(self):
        state = SourceState(path="/a.jsonl", offset=42)
        assert SourceState.from_dict(state.to_dict()) == state
        assert SourceState.from_dict({}) == SourceState()
