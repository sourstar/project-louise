"""The staged switch `GGML_SYCL_REORDER_EXPERTS` (deploy/localai/patches/
staged/0012-…): one comparison on the card, nothing changed by default.

docs/notes/backend-qualification-2026-11-21.md §3, "THE EIGHT ROWS". The
patch cannot be compiled where it is written, so what is held here is its
shape — the default is today's behaviour, one place is guarded — and that
the experiment it replaces is gone from `staged/` with its verdict
written."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"
PATCH = STAGED / "0012-llama-cpp-sycl-reorder-experts-switch.patch"
NOTE = (ROOT / "docs" / "notes" / "backend-qualification-2026-11-21.md").read_text(encoding="utf-8")


def _added(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]


def _engine_lines() -> str:
    outer = PATCH.read_text(encoding="utf-8")
    inner = "\n".join(_added(outer[outer.index("\n---\n"):]))
    return "\n".join(_added(inner))


def test_it_is_a_mail_under_localai_s_policy_and_adds_one_engine_patch():
    text = PATCH.read_text(encoding="utf-8")
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert re.findall(r"^diff --git a/(\S+)", text, re.M) == [
        "backend/cpp/llama-cpp/patches/0013-sycl-reorder-experts-switch.patch"]


def test_the_default_is_today_s_and_only_the_experts_reorder_is_guarded():
    added = _engine_lines()
    assert "int g_ggml_sycl_reorder_experts = 1;" in added
    assert 'ggml_sycl_get_env("GGML_SYCL_REORDER_EXPERTS", 1)' in added, "unset: reordered, as before"
    assert added.count("if (!g_ggml_sycl_reorder_experts) {") == 1, "one guard, in the experts' reorder"
    assert "USM_SYSTEM >= 2" not in added and "paged_by_use" not in added, "nothing of the retired experiment"


def test_the_retired_experiment_is_gone_and_its_verdict_written():
    assert not (STAGED / "0012-llama-cpp-sycl-usm-by-use.patch").exists()
    section = NOTE[NOTE.index("**THE EIGHT ROWS"):]
    section = section[:section.index("\n| configuration that fit |")]
    assert "The whole model is on the card after ONE answer" in section and "So the idea ends here" in section
    assert "UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY" in section, "why the neighbour died is on record"
    assert PATCH.name in section and "not yet a measurement" in section
    readme = (STAGED / "README.md").read_text(encoding="utf-8")
    assert PATCH.name in readme and "deleted" in readme
