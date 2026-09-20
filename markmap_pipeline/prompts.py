"""Prompts the updater sends to the model.

There is one for the initial build and one for incremental merges. The incremental prompt forbids
destroying the existing tree and asks for a JSON patch. Front matter is managed in Python and never sent.

Everything here is English on purpose so the prompt itself does not steer the model towards one language.
The map's language is not fixed by Python. It is detected in a separate, small model call (the language of the
[user] lines, or of the existing map when those are too short) and then stated explicitly in the system prompt.
Real runs showed why: Claude Code adds account context even in `-p` mode, and with only a rule such as "write
in the language of the [user] lines" English conversations still came out Japanese (3/3), while a bare
language question was answered correctly (3/3) and an explicit "write in English" was followed (3/3).
"""
from typing import Iterable, List, Optional

from .extract import Turn
from .markdown import headings

_COMMON_FORMAT_RULES = """\
Strict output format:
- Output only the Markdown body that Markmap renders. No preamble, no explanation, no closing remarks.
- Do not wrap the output in ``` code fences. Do not write YAML front matter.
- Headings (#, ##, ###) form the tree levels; bullet points (-) are the leaves.
- Summarize each node as a short noun phrase or one sentence. Do not paste long quotes or raw dialogue.
- Do not include tool logs, lists of file paths or code itself. Record topics, decisions, reasons and open issues."""

_LANGUAGE_RULE = """\
Language:
- First, silently determine the language of the text of the [user] lines in the conversation log. Write every heading and bullet, including the root title, in exactly that language, whatever it is.
- Determine that language only from that text. Ignore anything you know or are told about the account holder, the person running this command, or their locale; they are not the [user] in the log.
- The conversation log may be mixed: code, error messages and quotes in other languages do not change the map's language.
- If the [user] lines are too short to tell, use the language of the existing mind map."""

INITIAL_SYSTEM = """\
You are a summarization engine that builds a new Markmap mind map (Markdown) from the conversation log between a developer and an AI assistant.

Structure:
- There must be exactly one root heading (# ). The root is a short title that makes the topic of the conversation obvious at a glance (for example "Design and implementation of the herdr plugin" or "Investigating the billing API outage"). Never use the workspace name, directory name or agent name as-is.
- The main topics of the conversation become ## headings, with details under them as ### headings or bullet points.
- Give headings stable names that later additions can be filed under (avoid dates or order-dependent names such as "First question").
- If the conversation is still short, do not over-split: one to three ## headings are enough.

""" + _COMMON_FORMAT_RULES + "\n\n" + _LANGUAGE_RULE

INCREMENTAL_SYSTEM = """\
You are a summarization engine that adds new conversation content to an existing Markmap mind map (Markdown) as a patch.
Do not output the whole map. Return only the additions as JSON; the program inserts them into the existing map.

Output exactly one JSON object of this shape (no preamble, no explanation, no code fences):
{
  "root_title": null,
  "additions": [
    {"under": "## An existing heading", "items": ["- A new bullet", "### A new sub-heading", "- A bullet under it"]},
    {"new_section": "## A new topic", "items": ["- A bullet"]}
  ]
}

Rules:
- "under" must be one of the existing headings listed in <headings>, copied exactly, character for character. Do not invent headings that are not listed.
- File new content as "items" under the most closely related existing heading. Each item is one Markdown line: a "- " bullet, or a "### " (or deeper) sub-heading.
- Use "new_section" to add a new ## heading only for a topic that fits none of the existing headings. Keep such additions to a minimum.
- Do not add content that duplicates an existing bullet. You cannot delete, rename or reorder existing headings or bullets in the Markdown (the program keeps every heading below the root).
- No preamble, no commentary, no ``` code fences. Output the JSON object only.
- "root_title" is normally null. Only when the root title does not describe the topic of the conversation (for example it is a directory name or an agent name), or when the topic has broadened, may the root be retitled to a short title that makes the topic obvious; no other heading can be changed.
- If there is nothing to add, return an empty "additions" array.
- Summarize each item as a short noun phrase or one sentence. Do not include tool logs, lists of file paths or code itself. Record topics, decisions, reasons and open issues.

""" + _LANGUAGE_RULE + "\n"


def initial_system(language: Optional[str]) -> str:
    """The initial-build system prompt, stating the detected language when there is one."""
    return INITIAL_SYSTEM + language_directive(language, "the entire mind map")


def incremental_system(language: Optional[str]) -> str:
    """The merge system prompt, stating the detected language when there is one."""
    return INCREMENTAL_SYSTEM + language_directive(language, "all additions")


def language_directive(language: Optional[str], what: str) -> str:
    if not language:
        return ""
    return "\n\nThe [user] lines in this conversation are written in %s. Write %s in %s.\n" % (language, what, language)


DETECT_LANGUAGE_SYSTEM = """\
You identify the language a text is written in.
Answer with the English name of the language only (for example: English, Japanese, German). No other words, no punctuation.
Judge only the text inside <user_lines>. Ignore anything you know or are told about the account holder, the person running this command, or their locale; they are not the author of that text.
Code, file paths, error messages and quoted output inside the text do not count; judge the natural-language parts.
If <user_lines> has no natural-language text or is too short to tell, answer with the language of the headings inside <existing_headings> instead.
If neither can be determined, answer: unknown"""

_LANGUAGE_NAME_MAX_CHARS = 40


def build_detect_language_user(user_lines: Iterable[str], current_body: Optional[str]) -> str:
    text = "<user_lines>\n%s\n</user_lines>" % "\n".join(user_lines)
    if current_body:
        text += "\n\n<existing_headings>\n%s\n</existing_headings>" % "\n".join(headings(current_body))
    return text


def parse_language_name(raw: str) -> Optional[str]:
    """A bare language name from the detection answer, or None ("unknown", prose, several lines, garbage)."""
    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    name = lines[0].strip().rstrip(".").strip()
    if not name or len(name) > _LANGUAGE_NAME_MAX_CHARS or name.casefold() == "unknown":
        return None
    words = name.split()
    if len(words) > 3 or not all(word.replace("-", "").isalpha() for word in words):
        return None
    return name


def render_turns(turns: Iterable[Turn], max_turn_chars: int = 2000, max_total_chars: int = 20000) -> str:
    """Render turns as text for the model; long turns are truncated and, over the total budget, the newest are kept."""
    rendered: List[str] = []
    for turn in turns:
        text = turn.text
        if len(text) > max_turn_chars:
            text = text[:max_turn_chars] + "\n… (%d more characters omitted)" % (len(turn.text) - max_turn_chars)
        header = "[%s %s]" % (turn.role, turn.timestamp) if turn.timestamp else "[%s]" % turn.role
        rendered.append(header + "\n" + text)

    kept: List[str] = []
    total = 0
    for block in reversed(rendered):
        if kept and total + len(block) > max_total_chars:
            kept.append("… (%d earlier turns omitted)" % (len(rendered) - len(kept)))
            break
        kept.append(block)
        total += len(block)
    kept.reverse()
    return "\n\n".join(kept)


def build_initial_user(agent_name: str, turns_text: str) -> str:
    return (
        "Workspace name (context only; the root heading must be a title describing the topic, not this name): %s\n\n"
        "Below is the conversation log with this agent. Build a new mind map from it.\n\n"
        "<conversation>\n%s\n</conversation>" % (agent_name, turns_text)
    )


def build_incremental_user(current_body: str, turns_text: str) -> str:
    heading_list = "\n".join(headings(current_body))
    return (
        "This is the current mind map.\n\n"
        "<current_mindmap>\n%s\n</current_mindmap>\n\n"
        "Existing headings usable in \"under\" (copy these strings exactly):\n\n"
        "<headings>\n%s\n</headings>\n\n"
        "Below is the conversation log added since the last update. Output a JSON patch that files this content under the existing headings.\n\n"
        "<new_conversation>\n%s\n</new_conversation>" % (current_body.rstrip("\n"), heading_list, turns_text)
    )


def build_retry_note(missing: Iterable[str], patch_problem: Optional[str] = None) -> str:
    """Appended to the merge prompt when the previous answer was unusable, saying what was wrong with it."""
    lines = "\n".join("- " + h for h in missing)
    note = "\n\n[Retry instructions] "
    if patch_problem:
        note += "The previous output could not be read as a JSON patch (%s). Output the JSON object only, without preamble or code fences. " % patch_problem
    if lines:
        note += (
            "The previous output used headings in \"under\" that are not in <headings>. "
            "Copy a string from <headings> exactly, or use \"new_section\" when no existing heading fits. Offending entries:\n%s" % lines
        )
    return note
