"""The context on demand as prepared for upstream — deploy/localai/upstream/
llama-cpp-context-on-demand/ and localai-context-on-demand/, each a diff and
its READING-GUIDE.md (operator, 2026-11-11: "prepare everything for upstream,
I will upload it eventually") — is held to the form the two projects ask of
what a person files after an assistant wrote the code:

- llama.cpp (CONTRIBUTING.md, AGENTS.md): no AI-written commit message or
  description, so the files are DIFFS and nothing else; ASCII only;
- LocalAI (.agents/ai-coding-assistants.md): the human signs off and names the
  assistance, so no trailer is written for him; docs travel with the code.

And to the staged patch the node builds from: the same feature, test for test.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
UPSTREAM = ROOT / "deploy" / "localai" / "upstream"
LLAMA_DIR = UPSTREAM / "llama-cpp-context-on-demand"
LOCALAI_DIR = UPSTREAM / "localai-context-on-demand"
LLAMA = [LLAMA_DIR / name for name in (
    "1-context-on-demand.diff", "2-context-create-report.diff", "3-context-canary.diff")]
LOCALAI = LOCALAI_DIR / "context-on-demand.diff"
GUIDE = "READING-GUIDE.md"
STAGED = PATCHES / "staged" / "llama.cpp" / "0011-server-context-on-demand.patch"

# every line of `git diff`'s output starts with one of these: anything else is prose
DIFF_LINE = re.compile(
    r"^(diff --git |index [0-9a-f]+\.\.[0-9a-f]+|--- |\+\+\+ |@@ |[ +-]|\\ No newline"
    r"|new file mode |deleted file mode |old mode |new mode |similarity index |rename (from|to) |$)")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _paths(path: Path) -> list[str]:
    return re.findall(r"^diff --git a/(\S+) b/", _text(path), re.M)


def _tests_added(text: str) -> list[str]:
    return re.findall(r"^\+def (test_\w+)\(", text, re.M)


def test_the_material_is_there_and_nothing_else():
    assert all(path.is_file() for path in [*LLAMA, LOCALAI]), "a part is missing"
    assert sorted(p.name for p in LLAMA_DIR.iterdir()) == sorted([*(p.name for p in LLAMA), GUIDE])
    assert sorted(p.name for p in LOCALAI_DIR.iterdir()) == sorted([LOCALAI.name, GUIDE])


def test_each_file_is_a_diff_and_carries_no_message():
    """A commit message, a description or a mail header written here would be
    an AI-written post in the operator's name: upstream closes the PR for it."""
    for path in [*LLAMA, LOCALAI]:
        lines = _text(path).splitlines()
        assert lines[0].startswith("diff --git "), path.name
        prose = [line for line in lines if not DIFF_LINE.match(line)]
        assert not prose, (path.name, prose[:3])


def test_no_trailer_is_written_for_the_human():
    for path in [*LLAMA, LOCALAI]:
        assert not re.search(r"^[ +-]?\s*(Signed-off-by|Co-Authored-By|Assisted-by):",
                             _text(path), re.M | re.I), path.name


def test_what_goes_to_llama_cpp_is_ascii():
    """AGENTS.md: "Avoid ... any unicode characters"."""
    for path in LLAMA:
        added = [line for line in _text(path).splitlines() if line.startswith("+")]
        assert all(line.isascii() for line in added), path.name


def test_the_llama_cpp_parts_touch_common_and_the_server_only():
    for path in LLAMA:
        assert all(p.startswith(("common/", "tools/server/")) for p in _paths(path)), path.name


def test_the_parts_stand_on_each_other():
    """Part 1 creates the tests' file; 2 and 3 add to it."""
    tests = "tools/server/tests/unit/test_context_on_demand.py"
    created = [f"new file mode 100644\nindex 0000000" in _text(path).split(f"b/{tests}\n", 1)[1][:60]
               for path in LLAMA]
    assert created == [True, False, False]
    assert [len(_tests_added(_text(path))) for path in LLAMA] == [14, 2, 5]


def test_the_parts_hold_the_tests_of_the_staged_patch():
    """The node builds from the staged patch; what is filed is the same
    feature. One changed without the other shows here."""
    staged = _tests_added(_text(STAGED))
    parts = [name for path in LLAMA for name in _tests_added(_text(path))]
    assert len(parts) == len(set(parts))
    assert set(parts) == set(staged) and len(staged) == 21


def test_the_localai_diff_carries_its_docs_and_not_the_llama_cpp_patch():
    paths = _paths(LOCALAI)
    assert "docs/content/advanced/model-configuration.md" in paths, "LocalAI: docs with the code"
    assert not [p for p in paths if "/patches/" in p], \
        "upstream LocalAI gets the feature from its llama.cpp pin, not from a patch file"
    assert "backend/cpp/llama-cpp/context_on_demand_test.cpp" in paths


def test_each_guide_names_the_commit_its_diff_applies_to():
    """A diff without its base cannot be applied once upstream has moved."""
    for folder in (LLAMA_DIR, LOCALAI_DIR):
        assert re.search(r"master\s+`[0-9a-f]{40}`", _text(folder / GUIDE)), folder.name
