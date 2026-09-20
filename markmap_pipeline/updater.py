"""Incremental update of the mind map.

The initial build asks the backend for the whole map. Later merges ask for a JSON patch (additions only)
that Python inserts into the existing map, so headings below the root can never be lost and the model's
output stays small regardless of the map's size. While the model works, the new turns are shown under a
pending section so the map reflects the conversation immediately (two-phase update). The map's language is
detected by a separate small model call (language_backend) and stated explicitly in the prompts; the two
section labels Python writes itself are English.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

from .backends import BackendError
from .extract import Turn
from .files import atomic_write
from .markdown import (
    DEFAULT_FRONTMATTER,
    clean_model_output,
    headings,
    initial_document,
    is_skeleton,
    join_frontmatter,
    root_count,
    split_frontmatter,
)
from .patch import PatchError, append_unsorted, apply_patch, fallback_append, parse_patch, strip_pending, with_pending
from .prompts import (
    DETECT_LANGUAGE_SYSTEM,
    build_detect_language_user,
    build_incremental_user,
    build_initial_user,
    build_retry_note,
    incremental_system,
    initial_system,
    parse_language_name,
    render_turns,
)


@dataclass(frozen=True)
class UpdateResult:
    status: str  # "noop" | "created" | "updated" | "fallback" | "error"
    attempts: int = 0
    message: str = ""


class Updater:
    def __init__(
        self,
        backend,
        output_path: Union[str, Path],
        agent_name: str,
        max_retries: int = 1,
        max_turn_chars: int = 2000,
        max_total_chars: int = 20000,
        instant_placeholder: bool = True,
        merge_backend=None,
        language_backend=None,
    ):
        self.backend = backend
        self.merge_backend = merge_backend or backend  # incremental merges may use a cheaper/faster setting
        self.language_backend = language_backend  # judges the map language before each update; None skips detection
        self.output_path = Path(output_path)
        self.agent_name = agent_name
        self.max_retries = max_retries
        self.max_turn_chars = max_turn_chars
        self.max_total_chars = max_total_chars
        self.instant_placeholder = instant_placeholder

    def _read_current(self) -> Optional[str]:
        """The existing map without any pending section, or None (missing, empty or skeleton) for the initial build."""
        if not self.output_path.exists():
            return None
        frontmatter, body = split_frontmatter(self.output_path.read_text(encoding="utf-8"))
        content = join_frontmatter(frontmatter, strip_pending(body))
        return None if is_skeleton(content) else content

    def update(self, turns: Sequence[Turn]) -> UpdateResult:
        turns = list(turns)
        if not turns:
            return UpdateResult(status="noop")
        turns_text = render_turns(turns, self.max_turn_chars, self.max_total_chars)
        current = self._read_current()
        if self.instant_placeholder:
            self._show_pending(current, turns)
        language = self._detect_language(turns, current)
        if current is None:
            return self._create(turns, turns_text, language)
        return self._merge(current, turns, turns_text, language)

    def _detect_language(self, turns: Sequence[Turn], current: Optional[str]) -> Optional[str]:
        """Ask the language backend for the language of the [user] lines; None when unavailable or unusable."""
        if self.language_backend is None:
            return None
        body = split_frontmatter(current)[1] if current is not None else None
        user_lines = [t.text for t in turns if t.role == "user"]
        try:
            raw = self.language_backend.complete(DETECT_LANGUAGE_SYSTEM, build_detect_language_user(user_lines, body))
        except BackendError:
            return None
        return parse_language_name(raw)

    def _show_pending(self, current: Optional[str], turns: Sequence[Turn]) -> None:
        """Phase one: make the new turns visible immediately, before the model is called."""
        frontmatter, body = split_frontmatter(current if current is not None else initial_document(self.agent_name))
        atomic_write(self.output_path, join_frontmatter(frontmatter or DEFAULT_FRONTMATTER, with_pending(body, turns)))

    def _create(self, turns: Sequence[Turn], turns_text: str, language: Optional[str]) -> UpdateResult:
        output = clean_model_output(self.backend.complete(initial_system(language), build_initial_user(self.agent_name, turns_text)))
        if not headings(output) or root_count(output) != 1:
            _, skeleton_body = split_frontmatter(initial_document(self.agent_name))
            atomic_write(self.output_path, join_frontmatter(DEFAULT_FRONTMATTER, fallback_append(skeleton_body, turns)))
            return UpdateResult(status="fallback", attempts=1, message="Initial output had no headings or not exactly one root; fell back to the skeleton")
        atomic_write(self.output_path, join_frontmatter(DEFAULT_FRONTMATTER, output))
        return UpdateResult(status="created", attempts=1)

    def _merge(self, current: str, turns: Sequence[Turn], turns_text: str, language: Optional[str]) -> UpdateResult:
        """Phase two: ask for a JSON patch and insert it. Retries on unreadable patches or unknown headings."""
        frontmatter, body = split_frontmatter(current)
        base_user = build_incremental_user(body, turns_text)
        user = base_user
        attempts = 0
        applied: Optional[Tuple[str, List[dict]]] = None
        while attempts <= self.max_retries:
            attempts += 1
            raw = self.merge_backend.complete(incremental_system(language), user)
            try:
                patch = parse_patch(raw)
            except PatchError as exc:
                user = base_user + build_retry_note([], patch_problem=str(exc))
                continue
            new_body, unresolved = apply_patch(body, patch)
            applied = (new_body, unresolved)
            if not unresolved:
                atomic_write(self.output_path, join_frontmatter(frontmatter, new_body))
                return UpdateResult(status="updated", attempts=attempts)
            user = base_user + build_retry_note([a.get("under", "?") for a in unresolved])
        if applied is None:
            atomic_write(self.output_path, join_frontmatter(frontmatter, fallback_append(body, turns)))
            return UpdateResult(status="fallback", attempts=attempts, message="Model output was not a readable patch; appended the turns to the unsorted section instead")
        new_body, unresolved = applied
        leftovers = [line for addition in unresolved for line in addition.get("items", [])]
        atomic_write(self.output_path, join_frontmatter(frontmatter, append_unsorted(new_body, leftovers)))
        return UpdateResult(status="updated", attempts=attempts, message="Some additions named unknown headings and were appended to the unsorted section")
