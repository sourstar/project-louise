"""The staged experiment `GGML_SYCL_USM_SYSTEM=2` (deploy/localai/patches/
staged/0012-…): the 35B's weights paged to the card by use.

docs/notes/backend-qualification-2026-11-21.md §3, "Paging by use". The
patch cannot be compiled where it is written, so what can be held here is
its shape: it leaves the measured configuration (the value 1) alone, it
guards the two places that put every page on the card, and the note says
which rows decide whether it stays."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "deploy" / "localai" / "patches" / "staged" / "0012-llama-cpp-sycl-usm-by-use.patch"
NOTE = (ROOT / "docs" / "notes" / "backend-qualification-2026-11-21.md").read_text(encoding="utf-8")
README = (PATCH.parent / "README.md").read_text(encoding="utf-8")


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
        "backend/cpp/llama-cpp/patches/0013-sycl-usm-system-by-use.patch"]


def test_the_measured_value_is_left_as_it_was():
    added = _engine_lines()
    helper = re.search(r"static bool ggml_sycl_buffer_paged_by_use\(.*?\n\}", added, re.S)
    assert helper, "one test of the mode, used at both places"
    body = helper.group(0)
    assert "g_ggml_sycl_usm_system >= 2" in body, "the value 1 is the configuration München measured"
    assert "ggml_backend_buffer_is_sycl(buffer)" in body and "is_usm_system" in body, \
        "only a buffer of this backend that IS a system allocation"


def test_both_things_that_put_every_page_on_the_card_are_guarded():
    added = _engine_lines()
    # the load: written from the host, before the copy on the card
    assert re.search(r"if \(ggml_sycl_buffer_paged_by_use\(buffer\)\) \{\s+memcpy\(\(char \*\) tensor->data \+ offset, "
                     r"data, size\);\s+return;", added)
    # the first token: an expert tensor is not rewritten whole
    assert re.search(r"if \(ggml_sycl_buffer_paged_by_use\(src0->buffer\)\) \{\s+return;", added)
    assert added.count("ggml_sycl_buffer_paged_by_use(") == 3, "the definition and its two uses, no more"


def test_the_note_says_what_it_is_for_and_which_rows_decide():
    section = NOTE[NOTE.index("**Paging by use — where it stands"):]
    section = section[:section.index("\n| configuration that fit |")]
    assert PATCH.name in section and "NOT compiled here" in section
    assert "64 %" in section and "90 %" in section, "the measured concentration of expert use"
    assert len(re.findall(r"^\d\. ", section, re.M)) == 8, "the eight rows"
    assert "twenty different questions" in section and "Stop at any abort" in section
    assert PATCH.name in README and "AN EXPERIMENT" in README
