"""Section-level editing of the mind map body.

The body is split at every heading into sections (markdown.split_sections); the JSON patch the model
returns is inserted section by section, so headings below the root can never be lost. The two sections
the plugin writes itself (the unsorted fallback and the pending placeholder) are managed here too.
"""
import json
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .extract import Turn
from .markdown import HEADING_RE, Section, render_sections, split_sections, strip_blank_edges

# The two sections the plugin writes itself are always English. Earlier releases wrote them in Japanese;
# those titles are still recognized so an existing map is reused rather than given a second section.
UNSORTED_TITLE = "Unsorted"
PENDING_TITLE = "Latest (pending merge)"
LEGACY_SECTION_TITLES = ("未整理", "最新（統合待ち）")
UNSORTED_HEADING = "## " + UNSORTED_TITLE
PENDING_HEADING = "## " + PENDING_TITLE
_UNSORTED_TITLES = (UNSORTED_TITLE, LEGACY_SECTION_TITLES[0])
_PENDING_TITLES = (PENDING_TITLE, LEGACY_SECTION_TITLES[1])

PENDING_SUMMARY_CHARS = 120


# ---------------------------------------------------------------------------
# Locating sections
# ---------------------------------------------------------------------------


def _normalize_title(text: str) -> str:
    return " ".join(text.strip().lstrip("#").split()).casefold()


def _find_section(sections: List[Section], *titles: str, level: Optional[int] = None) -> Optional[int]:
    """Index of the first section whose title is one of the given variants, optionally at one level only."""
    wanted = {_normalize_title(title) for title in titles}
    for index, section in enumerate(sections):
        if section.level and (level is None or section.level == level) and _normalize_title(section.title) in wanted:
            return index
    return None


def _subtree_end(sections: List[Section], index: int) -> int:
    """Index just past the last section that belongs under sections[index]."""
    level = sections[index].level
    end = index + 1
    while end < len(sections) and sections[end].level > level:
        end += 1
    return end


def _parse_items(items: Sequence[str], min_heading_level: int) -> Tuple[List[str], List[Section]]:
    """Split patch items into direct content lines and new sub-sections; headings too shallow are demoted."""
    direct: List[str] = []
    subsections: List[Section] = []
    for raw in items:
        line = raw.rstrip()
        if not line.strip():
            continue
        match = HEADING_RE.match(line)
        if match:
            level = max(len(match.group(1)), min_heading_level)
            subsections.append(Section(level=level, title=match.group(2)))
        elif subsections:
            subsections[-1].lines.append(line)
        else:
            direct.append(line)
    return direct, subsections


# ---------------------------------------------------------------------------
# JSON patch produced by the model
# ---------------------------------------------------------------------------


class PatchError(Exception):
    """The model output could not be read as a patch."""


@dataclass
class Patch:
    root_title: Optional[str] = None
    additions: List[dict] = field(default_factory=list)


def parse_patch(text: str) -> Patch:
    """Parse the model output as a JSON patch, tolerating code fences and prose around the object."""
    candidate = text.strip()
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        raise PatchError("Output did not contain a JSON object")
    try:
        data = json.loads(candidate[start : end + 1])
    except ValueError as exc:
        raise PatchError("Output was not valid JSON: %s" % exc)
    if not isinstance(data, dict):
        raise PatchError("Output was not a JSON object")
    raw_additions = data.get("additions") or []
    if not isinstance(raw_additions, list):
        raise PatchError("'additions' must be a list")
    root_title = data.get("root_title")
    return Patch(
        root_title=root_title if isinstance(root_title, str) else None,
        additions=[_parse_addition(entry) for entry in raw_additions],
    )


def _parse_addition(entry) -> dict:
    if not isinstance(entry, dict):
        raise PatchError("Each addition must be an object")
    items = entry.get("items") or []
    if isinstance(items, str):
        items = items.splitlines()
    if not isinstance(items, list):
        raise PatchError("'items' must be a list of lines")
    addition = {key: entry[key] for key in ("under", "new_section") if isinstance(entry.get(key), str)}
    addition["items"] = [str(item) for item in items]
    return addition


def _clean_title(text: str) -> str:
    return text.strip().lstrip("#").strip()


def apply_patch(body: str, patch: Patch) -> Tuple[str, List[dict]]:
    """Insert the patch into the body. Returns (new body, additions that named an unknown heading)."""
    sections = split_sections(body)
    unresolved: List[dict] = []

    root_title = _clean_title(patch.root_title or "")
    if root_title:
        root = _first_root(sections)
        if root is not None:
            root.title = root_title

    for addition in patch.additions:
        items = [line for line in addition.get("items", []) if line.strip()]
        if not items:
            continue
        if "new_section" in addition:
            _insert_new_section(sections, _clean_title(addition["new_section"]), items)
        elif not _append_under(sections, addition.get("under", ""), items):
            unresolved.append(addition)
    return render_sections(sections), unresolved


def _first_root(sections: List[Section]) -> Optional[Section]:
    return next((section for section in sections if section.level == 1), None)


def _insert_new_section(sections: List[Section], title: str, items: Sequence[str]) -> None:
    """Add a new ## section, before the plugin's own sections so those stay last."""
    direct, subsections = _parse_items(items, min_heading_level=3)
    index = _own_sections_index(sections)
    sections[index:index] = [Section(level=2, title=title, lines=direct)] + subsections


def _append_under(sections: List[Section], under: str, items: Sequence[str]) -> bool:
    """File items under an existing heading. False when that heading does not exist."""
    index = _find_section(sections, under)
    if index is None:
        return False
    parent = sections[index]
    direct, subsections = _parse_items(items, min_heading_level=parent.level + 1)
    existing = {line.strip() for line in parent.lines}
    parent.lines = strip_blank_edges(parent.lines) + [line for line in direct if line.strip() not in existing]
    if subsections:
        end = _subtree_end(sections, index)
        sections[end:end] = subsections
    return True


# ---------------------------------------------------------------------------
# The plugin's own sections: unsorted fallback and pending placeholder
# ---------------------------------------------------------------------------


def _own_sections_index(sections: List[Section]) -> int:
    """Where new sections go: before the unsorted and pending sections, which always stay last."""
    index = _find_section(sections, *(_UNSORTED_TITLES + _PENDING_TITLES), level=2)
    return len(sections) if index is None else index


def append_unsorted(body: str, lines: Sequence[str]) -> str:
    """Append lines under the unsorted section; an existing one (current or legacy title) is reused, else one is created at the end."""
    lines = [line for line in lines if line.strip()]
    if not lines:
        return body
    sections = split_sections(body)
    index = _find_section(sections, *_UNSORTED_TITLES, level=2)
    if index is None:
        sections.append(Section(level=2, title=UNSORTED_TITLE, lines=list(lines)))
    else:
        sections[index].lines = strip_blank_edges(sections[index].lines) + list(lines)
    return render_sections(sections)


def fallback_append(body: str, turns: Sequence[Turn]) -> str:
    """Fallback when the model output is unusable: keep the body and append the turns as bullets under the unsorted section."""
    return append_unsorted(body, ["- %s: %s" % (t.role, " ".join(t.text.split())) for t in turns])


def _summarize(turn: Turn) -> str:
    first_line = turn.text.strip().splitlines()[0] if turn.text.strip() else ""
    if len(first_line) > PENDING_SUMMARY_CHARS:
        first_line = first_line[:PENDING_SUMMARY_CHARS] + "…"
    return "- %s: %s" % (turn.role, first_line)


def strip_pending(body: str) -> str:
    """Remove the pending section (current or legacy title); a body without one is returned unchanged."""
    sections = split_sections(body)
    index = _find_section(sections, *_PENDING_TITLES, level=2)
    if index is None:
        return body
    del sections[index : _subtree_end(sections, index)]
    return render_sections(sections)


def with_pending(body: str, turns: Sequence[Turn]) -> str:
    """Return the body with a pending section listing the turns that are about to be merged."""
    sections = split_sections(strip_pending(body))
    pending = Section(level=2, title=PENDING_TITLE, lines=[_summarize(t) for t in turns])
    index = _find_section(sections, *_UNSORTED_TITLES, level=2)
    sections.insert(len(sections) if index is None else index, pending)
    return render_sections(sections)
