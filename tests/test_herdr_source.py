from markmap_pipeline.sources import HerdrReadSource, SourceState, new_lines_since


class TestNewLinesSince:
    def test_first_snapshot_is_all_new(self):
        assert new_lines_since(None, ["a", "b"]) == ["a", "b"]

    def test_appended_lines_only(self):
        assert new_lines_since(["a", "b"], ["a", "b", "c", "d"]) == ["c", "d"]

    def test_scrolled_snapshot_finds_overlap(self):
        assert new_lines_since(["a", "b", "c"], ["b", "c", "d"]) == ["d"]

    def test_identical_snapshot_gives_nothing(self):
        assert new_lines_since(["a", "b"], ["a", "b"]) == []

    def test_no_overlap_treats_everything_as_new(self):
        assert new_lines_since(["a", "b"], ["x", "y"]) == ["x", "y"]

    def test_blank_lines_are_ignored_for_matching(self):
        assert new_lines_since(["a", "", "b"], ["a", "b", "", "c"]) == ["c"]


class TestHerdrReadSource:
    def test_poll_returns_single_transcript_turn_and_cursor(self):
        snapshots = ["line1\nline2\n", "line1\nline2\nline3\n"]
        src = HerdrReadSource("worker", reader=lambda target, lines: snapshots.pop(0))
        turns, state = src.poll(SourceState())
        assert len(turns) == 1
        assert turns[0].role == "transcript"
        assert turns[0].text == "line1\nline2"
        assert state.path == "herdr:worker"
        assert state.cursor is not None

        turns, state2 = src.poll(state)
        assert [t.text for t in turns] == ["line3"]

    def test_poll_without_changes_returns_no_turns(self):
        src = HerdrReadSource("worker", reader=lambda target, lines: "same\n")
        _, state = src.poll(SourceState())
        turns, state2 = src.poll(state)
        assert turns == []
        assert state2 == state

    def test_cursor_roundtrips_through_state_dict(self):
        src = HerdrReadSource("worker", reader=lambda t, l: "a\nb\n")
        _, state = src.poll(SourceState())
        restored = SourceState.from_dict(state.to_dict())
        assert restored == state
