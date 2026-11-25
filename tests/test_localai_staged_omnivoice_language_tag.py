"""Staged 0022: the speech port reads a language TAG as its language
(docs/notes/omnivoice-on-our-backend.md §14; the operator, 2026-11-25: "the
language/country tag must be aligned to the language to speak, otherwise the
voice has always a thick italian accent").

The model's table knows a language by its id or its English name; "it-IT",
"en_GB" and "IT" were read as NO language, in silence, and a text with no
language is spoken in the accent of the reference clip. The behaviour was
checked where it was written (19 cases standalone; one sentence end to end on
a CPU build, the same bytes for a tag as for its language). Held here: what
the patch asks and in which order, that what answered before answers still,
that an unknown language is said, and the candidate that carries it."""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"
PATCH = STAGED / "0022-llama-cpp-omnivoice-language-tag.patch"
SERIES = STAGED / "engine-lang.series"
BEFORE = STAGED / "engine-page.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
INNER = "backend/cpp/llama-cpp/patches/0021-mtmd-omnivoice-language-tag.patch"
NOTE = (ROOT / "docs" / "notes" / "omnivoice-on-our-backend.md").read_text(encoding="utf-8")
NAME = "aa88ab5c356a"


def _added(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]


def _removed(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("-") and not line.startswith("---")]


def _inner() -> str:
    outer = PATCH.read_text(encoding="utf-8")
    return "\n".join(_added(outer[outer.index("\n---\n"):]))


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def test_it_is_a_mail_under_localai_s_policy_and_adds_one_engine_patch():
    text = PATCH.read_text(encoding="utf-8")
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert re.findall(r"^diff --git a/(\S+)", text, re.M) == [INNER]
    assert set(re.findall(r"^diff --git a/(\S+)", _inner(), re.M)) == {
        "tools/mtmd/mtmd-gen-lang.h", "tools/mtmd/mtmd-helper-gen.cpp", "tools/mtmd/tests/test-gen-lang.cpp"}


def test_a_tag_is_asked_as_given_then_in_lower_case_then_by_its_first_subtag():
    added = "\n".join(_added(_inner()))
    body = added[added.index("static inline std::string mtmd_gen_ov_language("):
                 added.index("static inline bool mtmd_gen_ov_language_unknown(")]
    given = body.index("std::string id = ov::resolve_language(given);")
    lower = body.index("id = ov::resolve_language(low);")
    first = body.index('id = ov::resolve_language(low.substr(0, cut));')
    assert given < lower < first, "the table's own answer first: an id or a name is never cut"
    assert 'low.find_first_of("-_")' in body, "it-IT and en_GB"
    assert "cut > 0" in body, "a tag that begins with its separator has no first subtag"
    # the model's table is the reference's own file: asked, not edited
    assert "vendor/omnivoice/lang-map.h" not in re.findall(r"^diff --git a/(\S+)", _inner(), re.M)


def test_the_request_path_uses_it_and_says_a_language_the_model_does_not_name():
    inner = _inner()
    added, removed = "\n".join(_added(inner)), "\n".join(_removed(inner))
    assert 'lang     = ov::resolve_language(inp->lang ? inp->lang : "");' in removed
    assert 'lang     = mtmd_gen_ov_language(inp->lang ? inp->lang : "");' in added
    assert re.search(r'if \(mtmd_gen_ov_language_unknown\(inp->lang \? inp->lang : "", lang\)\) \{\n\s+LOG_WRN\(', added)
    assert "is not a language the model names, the text is spoken with no language" in added
    # "none" and "" are how a caller asks for no language: no complaint
    unknown = added[added.index("static inline bool mtmd_gen_ov_language_unknown("):]
    assert "if (!id.empty() || given.empty()) {\n        return false;" in unknown
    assert 'return low != "none";' in unknown
    # the summary line gains the language at its END: what read its earlier fields still does
    assert 'decoder %.2f s%s%s; language %s\\n"' in added
    assert 'lang.empty() ? "none" : lang.c_str());' in added


def test_the_standalone_test_holds_what_answered_before_and_what_was_read_as_none():
    added = "\n".join(_added(_inner()))
    cases = dict(re.findall(r'expect\("([^"]*)", "([^"]*)"', added))
    for same in ("it", "yue", "italian", "Italian", "", "none", "None"):
        assert same in cases
    assert cases["ambo-pasco quechua"] == "qva", "a NAME with a hyphen is found before any cut"
    assert {k: cases[k] for k in ("it-IT", "en-GB", "en_US", "pt-BR", "zh-Hans-CN", "IT", "EN-gb")} == {
        "it-IT": "it", "en-GB": "en", "en_US": "en", "pt-BR": "pt", "zh-Hans-CN": "zh", "IT": "it", "EN-gb": "en"}
    for unknown in ("xx-YY", "klingon", "-it", "none-IT"):
        assert re.search(rf'expect\("{re.escape(unknown)}", "", true\)', added), unknown
    assert len(re.findall(r"^\s+expect\(", added, re.M)) == 19


def test_the_candidate_is_the_paging_engine_plus_this_patch_and_its_name_is_held():
    assert _lines(SERIES) == _lines(BEFORE) + ["staged/0022-llama-cpp-omnivoice-language-tag.patch"]
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(SERIES), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == NAME


def test_the_note_says_what_was_measured_and_what_was_not():
    section = NOTE[NOTE.index("## 14. A language tag is its language"):]
    for said in ("`it-IT`", "MEASURED", "the bytes of `it`", "NOT measured", NAME, "engine-lang.series", "#881"):
        assert said in section, said
    # the card, 2026-11-25: a tag with a region gives the bytes of its language; the first take, whose seed never
    # reached the port, is said as what it was; and what the row still does not show
    card = next(line for line in section.splitlines() if line.startswith("| on the card, through LocalAI, in a recorded voice"))
    for said in ("MEASURED: IT HOLDS", "`en` → `ee74f805da39`", "`en-GB` → `ee74f805da39`, twice",
                 "No language → `7cf56dcd126e`", "`it` → `7cf56dcd126e`", "the profile's own `it-IT`, read as `it`",
                 "on `d531d5d804f1` `en-GB` gave `2092a8d3e98a`, the bytes of NO language",
                 "A FIRST TAKE OF THIS ROW SHOWED NOTHING OF THE BYTES", "a seed reaches the port through `params` only",
                 "NOT measured: the encoded-then-kept case in this row", "the operator's ear"):
        assert said in card, said
