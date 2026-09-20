"""The mind map document itself: front matter, the heading structure and cleanup of model output."""
from markmap_pipeline.markdown import (
    DEFAULT_FRONTMATTER,
    Section,
    clean_model_output,
    headings,
    initial_document,
    is_skeleton,
    join_frontmatter,
    render_sections,
    root_count,
    split_frontmatter,
    split_sections,
)

BODY = "# Root\n\n- intro\n\n## Topic A\n\n- a\n\n### Sub A1\n\n- a1\n\n## Topic B\n\n- b\n"


class TestSplitSections:
    def test_first_section_holds_the_preamble_before_any_heading(self):
        sections = split_sections("stray line\n\n# Root\n")
        assert sections[0].level == 0 and sections[0].lines == ["stray line", ""]
        assert sections[1].level == 1 and sections[1].title == "Root"

    def test_each_heading_starts_a_section_holding_the_lines_below_it(self):
        sections = split_sections(BODY)
        assert [(s.level, s.title) for s in sections[1:]] == [(1, "Root"), (2, "Topic A"), (3, "Sub A1"), (2, "Topic B")]
        assert [line for line in sections[2].lines if line.strip()] == ["- a"]

    def test_a_heading_inside_a_code_fence_is_content_not_a_heading(self):
        sections = split_sections("# Root\n\n```\n# not a heading\n```\n")
        assert [(s.level, s.title) for s in sections[1:]] == [(1, "Root")]
        assert "# not a heading" in sections[1].lines

    def test_heading_renders_back_in_its_markdown_form(self):
        assert Section(level=3, title="Sub A1").heading == "### Sub A1"


class TestRenderSections:
    def test_round_trips_a_body_with_one_blank_line_between_blocks(self):
        assert render_sections(split_sections(BODY)) == BODY

    def test_blank_lines_around_a_sections_content_are_normalized(self):
        rendered = render_sections([Section(level=2, title="A", lines=["", "", "- a", "", ""])])
        assert rendered == "## A\n\n- a\n"

    def test_an_empty_document_renders_as_empty_text(self):
        assert render_sections([]) == ""


class TestHeadings:
    def test_are_listed_in_order_and_normalized_to_hashes_plus_title(self):
        assert headings(BODY) == ["# Root", "## Topic A", "### Sub A1", "## Topic B"]

    def test_agree_with_split_sections_including_inside_code_fences(self):
        markdown = "# Root\n\n```py\n## fenced\n```\n\n## Real\n"
        assert headings(markdown) == [s.heading for s in split_sections(markdown) if s.level]
        assert headings(markdown) == ["# Root", "## Real"]

    def test_root_count_counts_only_level_one_headings(self):
        assert root_count(BODY) == 1
        assert root_count("# A\n\n# B\n") == 2
        assert root_count("## Only a topic\n") == 0


class TestFrontmatter:
    def test_is_split_from_the_body_and_joined_back_unchanged(self):
        document = DEFAULT_FRONTMATTER + BODY
        frontmatter, body = split_frontmatter(document)
        assert frontmatter == DEFAULT_FRONTMATTER and body == BODY
        assert join_frontmatter(frontmatter, body) == document

    def test_a_document_without_front_matter_is_all_body(self):
        assert split_frontmatter(BODY) == ("", BODY)


class TestSkeleton:
    def test_initial_document_is_front_matter_plus_a_root_heading(self):
        assert initial_document("worker") == DEFAULT_FRONTMATTER + "# worker\n"

    def test_a_document_with_only_a_root_heading_still_needs_the_initial_build(self):
        assert is_skeleton(initial_document("worker"))
        assert is_skeleton("")

    def test_a_document_with_content_is_not_a_skeleton(self):
        assert not is_skeleton(DEFAULT_FRONTMATTER + BODY)


class TestCleanModelOutput:
    def test_code_fences_and_preamble_before_the_first_heading_are_dropped(self):
        assert clean_model_output("Here you go:\n\n```markdown\n# Root\n\n- a\n```\n") == "# Root\n\n- a\n"

    def test_output_that_is_already_clean_only_gains_a_trailing_newline(self):
        assert clean_model_output("# Root\n\n- a") == "# Root\n\n- a\n"

    def test_a_closing_fence_left_by_a_preamble_is_dropped(self):
        # Seen from models that announce the map first: the opening fence is removed with the preamble,
        # and without this the stray ``` would end up as a node in the map.
        assert clean_model_output("Sure:\n\n```markdown\n# Root\n\n- a\n```\n") == "# Root\n\n- a\n"

    def test_a_balanced_code_block_inside_the_map_is_kept(self):
        assert clean_model_output("# Root\n\n```py\nx = 1\n```\n") == "# Root\n\n```py\nx = 1\n```\n"
