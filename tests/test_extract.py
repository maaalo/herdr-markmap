from pathlib import Path

import pytest

from markmap_pipeline.extract import Turn, extract_turns, read_new_turns, turn_from_record

FIXTURE = Path(__file__).parent / "fixtures" / "sample.jsonl"


def fixture_lines():
    return FIXTURE.read_text(encoding="utf-8").splitlines(keepends=True)


class TestTurnFromRecord:
    def test_user_string_content_becomes_user_turn(self):
        rec = {
            "type": "user",
            "message": {"role": "user", "content": "こんにちは"},
            "timestamp": "2026-09-18T00:00:00Z",
            "sessionId": "s",
        }
        turn = turn_from_record(rec)
        assert turn == Turn(role="user", text="こんにちは", timestamp="2026-09-18T00:00:00Z", message_id=None)

    def test_assistant_text_block_becomes_assistant_turn_with_message_id(self):
        rec = {
            "type": "assistant",
            "message": {"id": "msg_x", "role": "assistant", "content": [{"type": "text", "text": "回答"}]},
            "timestamp": "t",
        }
        turn = turn_from_record(rec)
        assert turn == Turn(role="assistant", text="回答", timestamp="t", message_id="msg_x")

    def test_assistant_multiple_text_blocks_are_joined(self):
        rec = {
            "type": "assistant",
            "message": {"id": "m", "role": "assistant", "content": [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]},
        }
        assert turn_from_record(rec).text == "A\nB"

    @pytest.mark.parametrize(
        "rec",
        [
            {"type": "assistant", "message": {"id": "m", "content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]}},
            {"type": "assistant", "message": {"id": "m", "content": [{"type": "thinking", "thinking": "x"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": "out"}]}},
            {"type": "user", "isMeta": True, "message": {"content": "meta"}},
            {"type": "user", "message": {"content": "<command-name>/clear</command-name>"}},
            {"type": "user", "message": {"content": "<local-command-stdout>x</local-command-stdout>"}},
            {"type": "user", "isSidechain": True, "message": {"content": "side"}},
            {"type": "assistant", "isSidechain": True, "message": {"id": "m", "content": [{"type": "text", "text": "side"}]}},
            {"type": "system", "subtype": "turn_duration"},
            {"type": "attachment", "attachment": {"type": "environment"}},
            {"type": "mode", "mode": "normal"},
            {"type": "user", "message": {"content": "   "}},
        ],
    )
    def test_non_conversation_records_are_skipped(self, rec):
        assert turn_from_record(rec) is None


class TestExtractTurns:
    def test_fixture_yields_conversation_only_in_order(self):
        turns = extract_turns(fixture_lines())
        assert [(t.role, t.text) for t in turns] == [
            ("user", "最初の質問です。"),
            ("assistant", "まず状況を確認します。"),
            ("assistant", "確認しました。\n結論は以下です。"),
            ("user", "二番目の質問です。"),
        ]

    def test_consecutive_assistant_lines_with_same_message_id_are_merged(self):
        turns = extract_turns(fixture_lines())
        merged = turns[2]
        assert merged.message_id == "msg_2"
        assert merged.timestamp == "2026-09-18T05:57:11.000Z"

    def test_malformed_and_blank_lines_are_ignored(self):
        turns = extract_turns(["not json\n", "\n", '{"type":"user","message":{"content":"ok"}}\n'])
        assert [t.text for t in turns] == ["ok"]


class TestReadNewTurns:
    def test_reads_from_offset_and_returns_new_offset(self, tmp_path):
        path = tmp_path / "s.jsonl"
        line1 = '{"type":"user","message":{"content":"one"}}\n'
        line2 = '{"type":"user","message":{"content":"two"}}\n'
        path.write_text(line1 + line2, encoding="utf-8")

        turns, offset = read_new_turns(path, 0)
        assert [t.text for t in turns] == ["one", "two"]
        assert offset == len((line1 + line2).encode("utf-8"))

        turns, offset2 = read_new_turns(path, offset)
        assert turns == []
        assert offset2 == offset

    def test_incomplete_trailing_line_is_left_for_next_read(self, tmp_path):
        path = tmp_path / "s.jsonl"
        line1 = '{"type":"user","message":{"content":"one"}}\n'
        partial = '{"type":"user","message":{"content":"tw'
        path.write_bytes((line1 + partial).encode("utf-8"))

        turns, offset = read_new_turns(path, 0)
        assert [t.text for t in turns] == ["one"]
        assert offset == len(line1.encode("utf-8"))

        path.write_bytes((line1 + partial + 'o"}}\n').encode("utf-8"))
        turns, offset = read_new_turns(path, offset)
        assert [t.text for t in turns] == ["two"]

    def test_offset_beyond_file_size_is_reset_to_zero(self, tmp_path):
        path = tmp_path / "s.jsonl"
        line1 = '{"type":"user","message":{"content":"one"}}\n'
        path.write_text(line1, encoding="utf-8")
        turns, offset = read_new_turns(path, 9999)
        assert [t.text for t in turns] == ["one"]
        assert offset == len(line1.encode("utf-8"))
