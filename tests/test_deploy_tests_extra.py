"""The deploy runs a release's own tests (München, 2026-11-27).

deploy/16f/deploy-louise-madlad-t5.sh was handed a release's tests-extra list
from bcdf0dc to d62c8eb and never read it: the block lived only in the node's
private copies of the script since af66d00, so a release's NEW test files
ran in the architecture's container alone, never against the staged image.
Here the block between its markers is run by bash itself, with `docker` and
`say` stood in for, against a staged tree in a temporary directory.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "16f" / "deploy-louise-madlad-t5.sh"


def _block() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    start, end = text.index("# >>> TESTS_EXTRA"), text.index("# <<< TESTS_EXTRA")
    return text[start:end]


def _run(tmp_path: Path, extra: str | None) -> tuple[int, str, list[str]]:
    staged = tmp_path / "root" / "src.new"
    (staged / "tests" / "ask").mkdir(parents=True)
    (staged / "tests" / "test_a.py").write_text("")
    (staged / "tests" / "ask" / "test_b.py").write_text("")
    calls = tmp_path / "docker-calls"
    stub = (
        'set -euo pipefail\n'
        f'ROOT={tmp_path / "root"}\nTEST_CPU_SHARES=8192\nTEST_BOUND=1800\n'
        'say() { echo "SAY $*"; }\n'
        f'docker() {{ printf "%s\\n" "$*" >> {calls}; }}\n'
    )
    env = {k: v for k, v in os.environ.items() if k != "TESTS_EXTRA"}
    if extra is not None:
        env["TESTS_EXTRA"] = extra
    r = subprocess.run(["bash", "-c", stub + _block()], env=env, capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout + r.stderr, calls.read_text().splitlines() if calls.exists() else []


def test_the_block_runs_before_the_staged_tree_is_proved():
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.index("# <<< TESTS_EXTRA") < text.index('if [ "${UNTIL:-}" = 3 ]; then')


def test_no_list_runs_nothing_more(tmp_path):
    rc, out, calls = _run(tmp_path, None)
    assert rc == 0 and calls == []


def test_a_list_file_runs_its_paths_in_the_staged_image(tmp_path):
    listing = tmp_path / "tests-extra-abc"
    listing.write_text("tests/test_a.py   # the new one\n# a comment line\ntests/ask/\n")
    rc, out, calls = _run(tmp_path, str(listing))
    assert rc == 0, out
    assert len(calls) == 1 and "louise-rag:staged" in calls[0] and "-m pytest" in calls[0]
    assert calls[0].split("no:cacheprovider", 1)[1].split() == ["tests/test_a.py", "tests/ask/"]
    assert "2 path(s)" in out


def test_the_paths_themselves_are_taken_too(tmp_path):
    rc, out, calls = _run(tmp_path, "tests/test_a.py tests/ask/test_b.py")
    assert rc == 0, out
    assert calls[0].split("no:cacheprovider", 1)[1].split() == ["tests/test_a.py", "tests/ask/test_b.py"]


@pytest.mark.parametrize("extra", ["tests/test_missing.py", "tests/test_a.py tests/nope/"])
def test_a_path_the_staged_tree_lacks_stops_the_deploy(tmp_path, extra):
    rc, out, calls = _run(tmp_path, extra)
    assert rc != 0 and "is not in the staged tree" in out and calls == []


def test_an_empty_list_stops_the_deploy_instead_of_running_every_test(tmp_path):
    listing = tmp_path / "tests-extra-empty"
    listing.write_text("# nothing yet\n\n")
    rc, out, calls = _run(tmp_path, str(listing))
    assert rc != 0 and "no test path" in out and calls == []


def test_a_conftest_in_the_list_is_dropped_and_said(tmp_path):
    """f4ebb4e, 2026-11-28: tests/acquire/conftest.py named as a path collided
    with tests/conftest.py at collection and the deploy stopped."""
    listing = tmp_path / "tests-extra-def"
    listing.write_text("tests/test_a.py\ntests/acquire/conftest.py\ntests/conftest.py\n")
    rc, out, calls = _run(tmp_path, str(listing))
    assert rc == 0, out
    assert "dropped (pytest loads them itself): tests/acquire/conftest.py tests/conftest.py" in out
    (call,) = calls
    assert call.split("cacheprovider ", 1)[1] == "tests/test_a.py"


def test_a_list_of_conftests_only_is_refused(tmp_path):
    listing = tmp_path / "tests-extra-ghi"
    listing.write_text("tests/acquire/conftest.py\n")
    rc, out, calls = _run(tmp_path, str(listing))
    assert rc == 1 and "no test path" in out and calls == []
