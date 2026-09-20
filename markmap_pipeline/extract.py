"""Extract conversation turns (user / assistant text) from a Claude Code session JSONL.

Tool calls, tool results, thinking, attachments, meta records and sub-agent (sidechain) records are
dropped. Everything is read-only; the watched file is never written to.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

# Internal markup recorded as user lines when a slash command runs
_COMMAND_PREFIXES = ("<command-name>", "<command-message>", "<local-command-stdout>", "<local-command-stderr>")


@dataclass(frozen=True)
class Turn:
    role: str  # "user" | "assistant"
    text: str
    timestamp: Optional[str] = None
    message_id: Optional[str] = None


def parse_record(line: str) -> Optional[dict]:
    """Parse one JSONL line into a dict. Broken or blank lines give None."""
    line = line.strip()
    if not line:
        return None
    try:
        record = json.loads(line)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def turn_from_record(record: dict) -> Optional[Turn]:
    """Convert one record into a Turn, or None when it is not conversation text."""
    if record.get("isSidechain") or record.get("isMeta"):
        return None
    record_type = record.get("type")
    message = record.get("message")
    if record_type not in ("user", "assistant") or not isinstance(message, dict):
        return None

    content = message.get("content")
    if record_type == "user":
        if not isinstance(content, str):
            return None  # Block arrays (tool_result etc.) are not conversation text
        text = content.strip()
        if not text or text.startswith(_COMMAND_PREFIXES):
            return None
        return Turn(role="user", text=text, timestamp=record.get("timestamp"), message_id=None)

    if not isinstance(content, list):
        return None
    texts = [
        block["text"].strip()
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip()
    ]
    if not texts:
        return None
    return Turn(role="assistant", text="\n".join(texts), timestamp=record.get("timestamp"), message_id=message.get("id"))


def extract_turns(lines: Iterable[str]) -> List[Turn]:
    """Build the list of Turns from lines; consecutive assistant lines with the same message_id are merged."""
    turns: List[Turn] = []
    for line in lines:
        record = parse_record(line)
        if record is None:
            continue
        turn = turn_from_record(record)
        if turn is None:
            continue
        if turns and _is_same_assistant_message(turns[-1], turn):
            previous = turns[-1]
            turns[-1] = Turn(
                role="assistant",
                text=previous.text + "\n" + turn.text,
                timestamp=previous.timestamp,
                message_id=previous.message_id,
            )
        else:
            turns.append(turn)
    return turns


def _is_same_assistant_message(previous: Turn, current: Turn) -> bool:
    return (
        previous.role == "assistant"
        and current.role == "assistant"
        and previous.message_id is not None
        and previous.message_id == current.message_id
    )


def read_new_turns(path: Union[str, Path], offset: int) -> Tuple[List[Turn], int]:
    """Read only the complete lines after `offset` bytes and return (turns, new offset).

    A trailing line without a newline is treated as still being written and left for the next read.
    If the file shrank (offset beyond its size), read from the start again.
    """
    path = Path(path)
    size = path.stat().st_size
    if offset < 0 or offset > size:
        offset = 0
    with path.open("rb") as f:
        f.seek(offset)
        data = f.read()
    last_newline = data.rfind(b"\n")
    if last_newline < 0:
        return [], offset
    complete = data[: last_newline + 1]
    lines = complete.decode("utf-8", errors="replace").splitlines(keepends=True)
    return extract_turns(lines), offset + len(complete)
