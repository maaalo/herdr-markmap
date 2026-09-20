"""The mind map document: front matter, the heading structure, model-output cleanup and the skeleton.

Pure string processing on Markmap-flavoured Markdown: no model, no herdr, no file I/O. `split_sections`
is the single place that reads the heading structure, code fences included, so the heading list and the
section-level editing in patch.py can never disagree about what counts as a heading.
"""
import re
from dataclasses import dataclass, field
from typing import List, Tuple

DEFAULT_FRONTMATTER = "---\nmarkmap:\n  colorFreezeLevel: 2\n  initialExpandLevel: 3\n---\n"
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


# ---------------------------------------------------------------------------
# Heading structure
# ---------------------------------------------------------------------------


@dataclass
class Section:
    """A heading and the lines directly below it. Level 0 is the preamble before the first heading."""

    level: int
    title: str
    lines: List[str] = field(default_factory=list)

    @property
    def heading(self) -> str:
        return "%s %s" % ("#" * self.level, self.title)


def split_sections(markdown: str) -> List[Section]:
    """Split the text at every heading. Headings inside code fences are content, not structure."""
    sections = [Section(level=0, title="")]
    in_fence = False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else HEADING_RE.match(line)
        if match:
            sections.append(Section(level=len(match.group(1)), title=match.group(2)))
        else:
            sections[-1].lines.append(line)
    return sections


def render_sections(sections: List[Section]) -> str:
    """Join sections back into Markdown, one blank line between blocks."""
    out: List[str] = []
    for section in sections:
        content = strip_blank_edges(section.lines)
        if section.level:
            out.append(section.heading)
            out.append("")
        if content:
            out.extend(content)
            out.append("")
    text = "\n".join(out).rstrip("\n")
    return text + "\n" if text else ""


def strip_blank_edges(lines: List[str]) -> List[str]:
    """The lines without the blank ones at either end."""
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def headings(markdown: str) -> List[str]:
    """Return heading lines in order, normalized to `# Title`."""
    return [section.heading for section in split_sections(markdown) if section.level]


def root_count(markdown: str) -> int:
    """Number of root headings (`# `). A mind map must have exactly one."""
    return sum(1 for section in split_sections(markdown) if section.level == 1)


# ---------------------------------------------------------------------------
# Front matter and the document as a whole
# ---------------------------------------------------------------------------


def split_frontmatter(markdown: str) -> Tuple[str, str]:
    """Split leading YAML front matter from the body; ("", body) when there is none."""
    if markdown.startswith("---\n"):
        end = markdown.find("\n---\n", 4)
        if end >= 0:
            return markdown[: end + 5], markdown[end + 5 :]
    return "", markdown


def join_frontmatter(frontmatter: str, body: str) -> str:
    return frontmatter + body


def is_skeleton(markdown: str) -> bool:
    """True when the body is empty or only a root heading, i.e. a skeleton that needs the initial build."""
    _, body = split_frontmatter(markdown)
    lines = [line for line in body.splitlines() if line.strip()]
    return not lines or (len(lines) == 1 and bool(HEADING_RE.match(lines[0])))


def clean_model_output(text: str) -> str:
    """Strip code fences and any preamble so the Markdown starts at its first heading."""
    text = text.strip("\n")
    fence = re.match(r"^```[^\n]*\n(.*?)\n?```\s*$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    lines = _drop_preamble(text.splitlines())
    lines = _drop_orphan_closing_fence(lines)
    return "\n".join(lines).strip("\n") + "\n"


def _drop_preamble(lines: List[str]) -> List[str]:
    """The lines from the first heading on; a model that explains itself before the map loses the explanation."""
    for index, line in enumerate(lines):
        if HEADING_RE.match(line):
            return lines[index:]
    return lines


def _is_fence(line: str) -> bool:
    return line.strip().startswith("```")


def _drop_orphan_closing_fence(lines: List[str]) -> List[str]:
    """Drop a trailing ``` left behind when the preamble hid the opening fence; balanced fences are kept."""
    content = strip_blank_edges(lines)
    if content and _is_fence(content[-1]) and sum(1 for line in content if _is_fence(line)) % 2 == 1:
        return content[:-1]
    return lines


def initial_document(title: str) -> str:
    """The smallest document markmap can render: front matter plus a root heading."""
    return join_frontmatter(DEFAULT_FRONTMATTER, "# %s\n" % title)
