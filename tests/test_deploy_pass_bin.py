"""The deploy installs the pass supervisor and its release where the passes
run them (deploy/16f/deploy-louise-madlad-t5.sh, step 4b). Until 2026-11-20
/root/supervise/bin/supervise-pass.sh was a copy no deploy updated, and
#779's memory hold reached it by hand (München). Run here against a
stand-in ROOT and PASS_BIN: the step's own lines, cut out of the script."""
from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "16f" / "deploy-louise-madlad-t5.sh"


def _step() -> str:
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('say "4b.'))
    end = next(i for i in range(start, len(lines)) if lines[i] == "done")
    return "\n".join(lines[start:end + 1])


def _run(tmp_path: Path, commit: str) -> str:
    script = "set -euo pipefail\nsay() { echo \"=== $*\"; }\n" + _step()
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=False,
                       env={"PATH": "/usr/bin:/bin", "ROOT": str(tmp_path / "root"),
                            "PASS_BIN": str(tmp_path / "bin"), "COMMIT": commit})
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_the_supervisor_and_its_release_are_installed_and_the_one_before_kept(tmp_path):
    deploy = tmp_path / "root" / "src" / "deploy"
    deploy.mkdir(parents=True)
    for f in ("supervise-pass.sh", "release-oom.sh"):
        (deploy / f).write_text(f"# {f} v1\n")
    out = _run(tmp_path, "aaa")
    assert "supervise-pass.sh installed" in out and "release-oom.sh installed" in out
    sup = tmp_path / "bin" / "supervise-pass.sh"
    assert sup.read_text() == "# supervise-pass.sh v1\n" and sup.stat().st_mode & 0o777 == 0o755
    assert not list((tmp_path / "bin").glob("*.pre-*")), "nothing was there before"
    assert "supervise-pass.sh unchanged" in _run(tmp_path, "bbb")
    with open(sup, encoding="utf-8") as running:  # a pass that runs the one before
        (deploy / "supervise-pass.sh").write_text("# supervise-pass.sh v2\n")
        out = _run(tmp_path, "ccc")
        assert running.read() == "# supervise-pass.sh v1\n", "installed by rename: the running pass reads on"
    assert "kept as supervise-pass.sh.pre-ccc" in out and "release-oom.sh unchanged" in out
    assert sup.read_text() == "# supervise-pass.sh v2\n", "the next pass runs the new one"
    assert (tmp_path / "bin" / "supervise-pass.sh.pre-ccc").read_text() == "# supervise-pass.sh v1\n"
    assert not list((tmp_path / "bin").glob("*.new.*")), "no temp file left"
