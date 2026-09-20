"""User-facing strings (logs, errors, notifications, action output) and code comments must be English.

Prompts are English too (a Japanese prompt steers the model into Japanese); only legacy section titles are exempt.
"""
import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿＀-￯]")
PACKAGE = ROOT / "markmap_pipeline"
MODULES = sorted(p.name for p in PACKAGE.glob("*.py") if p.name != "__init__.py")

from markmap_pipeline.patch import LEGACY_SECTION_TITLES

# Japanese section titles written by earlier releases; kept only so existing maps are still recognized.
ALLOWED_CONTENT_STRINGS = set(LEGACY_SECTION_TITLES)


def message_strings(path: Path):
    """Yield (line number, text) for every string literal that is not a docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant):
                docstring_nodes.add(id(node.body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_nodes:
            yield node.lineno, node.value


@pytest.mark.parametrize("module", MODULES)
def test_python_user_facing_strings_are_english(module):
    offenders = [
        "%s:%d: %r" % (module, lineno, text)
        for lineno, text in message_strings(PACKAGE / module)
        if CJK.search(text) and text not in ALLOWED_CONTENT_STRINGS
    ]
    assert offenders == []


def _comments_and_docstrings(path: Path):
    src = path.read_text(encoding="utf-8")
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            yield tok.start[0], tok.string
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                yield node.body[0].lineno, doc


ALL_PYTHON = sorted(list((ROOT / "markmap_pipeline").glob("*.py")) + list((ROOT / "tests").glob("*.py")))


@pytest.mark.parametrize("path", ALL_PYTHON, ids=lambda p: "%s/%s" % (p.parent.name, p.name))
def test_code_comments_and_docstrings_are_english(path):
    offenders = ["%s:%d" % (path.name, lineno) for lineno, text in _comments_and_docstrings(path) if CJK.search(text)]
    assert offenders == []
