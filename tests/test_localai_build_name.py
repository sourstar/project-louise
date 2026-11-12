"""deploy/localai/llama-cpp/build.sh names an engine by its series: the
digest of the series file (its `base` line, LocalAI's commit, included) and
its patches. The context-on-request session's finding, 2026-11-12: a
RELATIVE `SERIES_FILE` was lost by the `cd "$PATCHES"` the digest runs in,
cat's error was hidden by the pipe, and the name covered the patches alone —
c4e6133a6e85 and 9d17b8a39845 were named so. Two bases with the same patches
would have shared a name. `NAME_ONLY=1` prints the digest and stops."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
PATCHES = ROOT / "deploy" / "localai" / "patches"


def _name(series: str | Path, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, SERIES_FILE=str(series), NAME_ONLY="1")
    return subprocess.run(["sh", str(BUILD), "cpu", "/nonexistent-out"], cwd=cwd, env=env,
                          capture_output=True, text=True)


def test_a_relative_and_an_absolute_series_file_name_the_same_engine(tmp_path):
    staged = PATCHES / "staged" / "context-on-demand.series"
    absolute = _name(staged, tmp_path)
    relative = _name(os.path.relpath(staged, ROOT), ROOT)
    assert absolute.returncode == 0 and relative.returncode == 0, (absolute.stderr, relative.stderr)
    assert absolute.stdout == relative.stdout and len(absolute.stdout.strip()) == 12
    # and not the patches-only name the relative path gave before
    assert absolute.stdout.strip() != "c4e6133a6e85"


def test_the_name_covers_the_base(tmp_path):
    lines = (PATCHES / "series").read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("base ") for line in lines)
    moved = [("base 0123456789abcdef0123456789abcdef01234567" if line.startswith("base ") else line)
             for line in lines]
    other = tmp_path / "moved.series"
    other.write_text("\n".join(moved) + "\n", encoding="utf-8")
    a, b = _name(PATCHES / "series", tmp_path), _name(other, tmp_path)
    assert a.returncode == 0 and b.returncode == 0, (a.stderr, b.stderr)
    assert a.stdout != b.stdout, "the same patches on another base are another engine"


def test_an_unreadable_series_or_patch_stops_the_build(tmp_path):
    missing = _name(tmp_path / "no-such.series", tmp_path)
    assert missing.returncode == 2 and "series file not readable" in missing.stderr
    bad = tmp_path / "bad.series"
    bad.write_text("base 0123456789abcdef0123456789abcdef01234567\nno-such.patch\n", encoding="utf-8")
    gone = _name(bad, tmp_path)
    assert gone.returncode == 2 and "patch not readable" in gone.stderr
