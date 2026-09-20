"""File names and persistence shared by every module that stores something (map, registry, state, config).

Writes go through a temporary file and a rename so readers (markmap -w included) never see a partial file.
Reads are forgiving: a missing or damaged file reads as "nothing stored yet" rather than raising.
"""
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional, Union

Pathish = Union[str, Path]


def safe_name(value: str) -> str:
    """Make a string usable as a file name; pane ids and workspace labels may contain anything."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)


def atomic_write(path: Pathish, text: str) -> None:
    """Write to a temporary file and rename it into place so readers never see a partial file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json(path: Pathish) -> Optional[dict]:
    """The JSON object stored at path, or None when it is missing, unreadable or not an object."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path: Pathish, data: dict) -> None:
    """Store a JSON object atomically, indented and unescaped so it stays readable and editable by hand."""
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
