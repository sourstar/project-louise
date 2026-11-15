"""tools/code_pin.py: a run's tag pins the code the run imports (2026-11-16:
the chunker and retrieval tags pinned Ask's code, which they do not run)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import code_pin  # noqa: E402


def _repo(root: Path) -> Path:
    """app/ a package, tools/ with a namespace package (no __init__.py, as
    tools/measure is), and an entry importing at the top, inside a function
    and relatively."""
    for rel, text in {
        "app/__init__.py": "",
        "app/a.py": "from .c import f\nimport numpy\n",
        "app/b.py": "X = 1\n",
        "app/c.py": "def f():\n    return 1\n",
        "app/pkg/__init__.py": "",
        "app/pkg/deep.py": "Y = 2\n",
        "tools/__init__.py": "",
        "tools/measure/x.py": "from app.pkg import deep\n",
        "tools/run.py": ("import json\nfrom app import a\n\n"
                         "def later():\n    from tools.measure import x\n    return x\n"),
    }.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root / "tools" / "run.py"


def test_the_closure_follows_top_level_lazy_and_relative_imports_into_the_repository(tmp_path):
    entry = _repo(tmp_path)
    got = {p.relative_to(tmp_path.resolve()).as_posix() for p in code_pin.closure(entry, tmp_path)}
    assert {"tools/run.py", "app/__init__.py", "app/a.py", "app/c.py", "tools/measure/x.py",
            "app/pkg/__init__.py", "app/pkg/deep.py"} <= got, \
        "a lazy import into a namespace package, and a relative one, are followed"
    assert "app/b.py" not in got, "a module nothing imports is not the run's code"


def test_the_digest_moves_with_the_code_the_run_imports_and_only_with_it(tmp_path):
    entry = _repo(tmp_path)
    before = code_pin.digest(entry, tmp_path)
    (tmp_path / "app" / "b.py").write_text("X = 2\n", encoding="utf-8")
    assert code_pin.digest(entry, tmp_path) == before, "Ask's deploy, say: not this run's code"
    (tmp_path / "app" / "pkg" / "deep.py").write_text("Y = 3\n", encoding="utf-8")
    assert code_pin.digest(entry, tmp_path) != before, "a module imported inside a function"


def test_the_pilot_tools_pin_their_chunkers_search_and_scoring():
    """What the hand list named is still pinned, and the scoring module it
    missed now is."""
    for entry in ("tools/legal_pilot.py", "tools/chunker_mode.py", "tools/chunker_languages.py"):
        got = {p.relative_to(code_pin.ROOT).as_posix() for p in code_pin.closure(ROOT / entry)}
        assert {"app/chunking.py", "app/chunking_legal.py", "app/layers.py", "app/fts.py",
                "app/retrieval.py", "tools/legal_pilot.py", "tools/chunker_mode.py", entry} <= got, entry


def test_a_retrieval_tag_carries_the_run_code_digest():
    from tools import legal_pilot as lp
    tag = lp.pilot_tag(reranker="from:gutenberg", k=20, pool_name="p", as_of=None)
    assert tag.endswith(f"-code-{code_pin.digest(ROOT / 'tools' / 'legal_pilot.py')[7:19]}")
