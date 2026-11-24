"""The deploy's test runs against the node's own load (deploy/16f/
deploy-louise-madlad-t5.sh; München, 2026-11-21: #789's first two tries
died at 95 % and 65 % of a 900 s kill bound, the host at 15-16 on 12
cores). Every pytest the deploy runs in the staged image does so on a CPU
weight above the node's containers', under a bound for a run that hangs."""
from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "16f" / "deploy-louise-madlad-t5.sh"


def _commands() -> list[str]:
    """Each shell command of the script, continuation lines joined."""
    out, cur = [], ""
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        cur += line.rstrip("\\") + " "
        if not line.endswith("\\"):
            out.append(cur)
            cur = ""
    return out


def test_every_staged_pytest_runs_on_its_own_cpu_weight_under_its_bound():
    runs = [c for c in _commands() if "louise-rag:staged" in c and "python -m pytest" in c]
    assert len(runs) >= 5
    for c in runs:
        assert '--cpu-shares "$TEST_CPU_SHARES"' in c, c[:200]
        assert 'timeout -s KILL "$TEST_BOUND"' in c, c[:200]
    assert not re.search(r"timeout -s KILL \d", SCRIPT.read_text(encoding="utf-8")), "no bound left as a number"


def test_the_weight_is_above_the_nodes_and_the_bound_holds_a_busy_run():
    text = SCRIPT.read_text(encoding="utf-8")
    shares = int(re.search(r"^TEST_CPU_SHARES=\$\{TEST_CPU_SHARES:-(\d+)\}$", text, re.MULTILINE)[1])
    bound = int(re.search(r"^TEST_BOUND=\$\{TEST_BOUND:-(\d+)\}$", text, re.MULTILINE)[1])
    assert shares > 1024, "docker's default, which the node's containers run at"
    assert bound >= 1800, "the list took 606 s on a quiet node and passed 900 only in acquire-only"
