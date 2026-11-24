"""The staged engine fix `0013-llama-cpp-sycl-pool-refusal.patch`: a
device-memory refusal while a request runs fails that request, not the
backend (docs/notes/room-on-the-card.md §4; localai-bugs.md B18 / P14).

It compiles where it is written and cannot run there, so its proof is the card's (the
note's five steps). Held here: its shape — the pool throws instead of
aborting, one place catches and answers ALLOC_FAILED — and that the page
rule's switch-on waits for it, in writing."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"
PATCH = STAGED / "0013-llama-cpp-sycl-pool-refusal.patch"
NOTE = (ROOT / "docs" / "notes" / "room-on-the-card.md").read_text(encoding="utf-8")
ACTIONS = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")


def _added(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]


def _removed(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("-") and not line.startswith("---")]


def _inner() -> str:
    outer = PATCH.read_text(encoding="utf-8")
    return "\n".join(_added(outer[outer.index("\n---\n"):]))


def test_it_is_a_mail_under_localai_s_policy_and_adds_one_engine_patch():
    text = PATCH.read_text(encoding="utf-8")
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert re.findall(r"^diff --git a/(\S+)", text, re.M) == [
        "backend/cpp/llama-cpp/patches/0014-sycl-pool-refusal-fails-the-graph.patch"]


def test_the_pool_throws_where_it_aborted_and_one_place_answers_alloc_failed():
    inner = _inner()
    added, removed = "\n".join(_added(inner)), "\n".join(_removed(inner))
    assert "SYCL_CHECK(CHECK_TRY_ERROR(phys.emplace(dev, ctx, reserve_size)));" in removed, "the abort is gone"
    assert re.search(r"try \{\s+phys\.emplace\(dev, ctx, reserve_size\);\s+\} catch \(const sycl::exception & e\) \{",
                     added)
    assert 'throw ggml_sycl_out_of_device_memory(' in added
    assert "struct ggml_sycl_out_of_device_memory : public std::runtime_error" in added, \
        "not a sycl::exception: the function-try-blocks that exit on one must not see it"
    assert re.search(r"catch \(const ggml_sycl_out_of_device_memory & e\) \{.*?return GGML_STATUS_ALLOC_FAILED;",
                     added, re.S)
    assert added.count("return ggml_sycl_graph_compute_or_fail(sycl_ctx, cgraph);") == 2, "both plain paths"
    assert removed.count("ggml_backend_sycl_graph_compute_impl(sycl_ctx, cgraph);") == 2


def test_the_card_s_test_is_written_and_the_page_rule_waits_for_the_fix():
    section = NOTE[NOTE.index("## 4. "):]
    assert PATCH.name in section and "Compiled here, never run; its test is on the card" in section
    assert len(re.findall(r"^\d\. ", section, re.M)) == 5
    assert "one RERANKER" in section and "the sixteen fingerprints" in section, \
        "the pool is every model's; and a new build is a new run, gated before it serves"
    assert "the\n   backend's pid is the same afterwards" in section or "backend's pid is the same" in section
    assert 'Until it is in the live engine, `make_room = "page"` stays off' in section
    assert "| B18 | defect |" in ACTIONS and "### B18 and P14 (staged). " in ACTIONS
    row = re.search(r"^\| \*\*P14 \(staged\)\*\* \|.*$", ACTIONS, re.M)
    assert row and PATCH.name in row.group(0) and "to file upstream" in row.group(0)
    assert PATCH.name in (STAGED / "README.md").read_text(encoding="utf-8")
