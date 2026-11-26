"""The upstream reports written out to be filed as they are
(docs/notes/upstream-reports/, 2026-11-26; the operator files them).

An issue goes to a public tracker under the operator's name. What is held
here: every defect of the bug log that is upstream's and not filed has its
issue; an issue names nothing of this project, its node or its people; and
what an issue quotes of LocalAI's code is in the patch that fixes it."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "docs" / "notes" / "upstream-reports"
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"
LOCALAI = (REPORTS / "mudler-localai.md").read_text(encoding="utf-8")
LLAMA = (REPORTS / "ggml-llama-cpp-sycl.md").read_text(encoding="utf-8")
BLOCK = re.compile(r"<!-- issue:(B\d+) -->\n(.*?)\n<!-- /issue -->", re.S)
ISSUES = dict(BLOCK.findall(LOCALAI)) | dict(BLOCK.findall(LLAMA))

#: nothing of the project, its node or its people in a public issue (CLAUDE.md invariant 8 is about secrets;
#: this is the same care for names)
PRIVATE = ("Louise", "louise", "München", "Munchen", "Tensor", "sourstar", "VeGa", "vega", "keeper",
           "192.168.", "localai-bugs", "deploy/", "docs/notes", "Claude", "the node", "TrueNAS")


def test_every_unfiled_upstream_defect_after_b14_has_its_issue():
    assert sorted(dict(BLOCK.findall(LOCALAI)), key=lambda b: int(b[1:])) == \
        ["B17", "B19", "B20", "B21", "B22", "B24", "B25", "B26"]
    assert list(dict(BLOCK.findall(LLAMA))) == ["B18"]
    # and why the others have none
    for said in ("B15 is fixed upstream (v5.0.0, #13186)", "B16 is known to\nllama.cpp (#5763, #26565)",
                 "B23 was a defect of our\nown patch, not of LocalAI"):
        assert said in LOCALAI, said
    bugs = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")
    rows = {m.group(1) for m in re.finditer(r"^\| (B\d+) \| defect", bugs, re.M)}
    after_14 = {b for b in rows if int(b[1:]) > 14}
    assert after_14 == set(ISSUES) | {"B15", "B16", "B23"}, "a defect logged since has neither an issue nor a reason"
    assert "B17–B26 written out to\nbe filed as they are" in bugs


@pytest.mark.parametrize("bug", sorted(ISSUES))
def test_an_issue_is_a_title_and_a_body_and_names_nothing_of_ours(bug):
    title, _, body = ISSUES[bug].partition("\n")
    assert 40 <= len(title) <= 200 and not title.startswith(("#", "*", "<")), title
    assert body.startswith("\n**"), "a blank line, then the body's first heading"
    for word in PRIVATE:
        assert word not in ISSUES[bug], f"{bug}: {word!r} is ours, not for a public tracker"
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", ISSUES[bug]), "no address"
    # it says what version it is about, and what is asked
    assert "v5.0.1" in ISSUES[bug] or "78e0529" in ISSUES[bug]
    assert any(h in body for h in ("**Suggested fix.**", "**Fix we run.**", "**Describe the solution you'd like.**"))


def test_each_issue_has_its_line_for_the_filer_outside_the_issue():
    for bug in ISSUES:
        text = LOCALAI if bug != "B18" else LLAMA
        before = text[:text.index(f"<!-- issue:{bug} -->")]
        assert "*For the filer" in before[-1400:], bug
    # the patches the filer is pointed to exist (in what was written out that day)
    written = LOCALAI[LOCALAI.index("# After B14: found on v5.0.1"):] + \
        LLAMA[LLAMA.index("# A device-memory refusal at the pool's commit aborts the process"):]
    named = re.findall(r"(00\d\d-[a-z0-9-]+\.patch)", written)
    assert len(set(named)) == 7, sorted(set(named))
    for name in named:
        assert (STAGED / name).is_file(), name


def test_what_an_issue_quotes_of_the_code_is_in_the_patch_that_fixes_it():
    def patch(name: str) -> str:
        return (STAGED / name).read_text(encoding="utf-8")
    # B21: the fix proposed is the fix carried
    stream = patch("0025-llama-cpp-stream-fails-after-its-first-chunk.patch")
    for line in ("if (result->is_error()) {", "json error_json = result->to_json();",
                 'return grpc::Status(grpc::StatusCode::INTERNAL, error_json.value("message", "Error occurred"));'):
        assert line in ISSUES["B21"] and "+" + " " * 12 + line.split("\n")[0] in stream or line in stream, line
    # B22: the lines quoted are the backend's own, as the park patch removes or keeps them
    park = patch("0023-llama-cpp-park-when-told.patch")
    for line in ("if (loaded_model_identity == request->model()) {",
                 "// Same model: a repeated request is a no-op, not a reload.",
                 'result->set_message("Model already loaded");'):
        assert line in ISSUES["B22"] and line in park, line
    # B19: the handler's own words, and the two calls of the fix
    second = patch("0014-llama-cpp-second-signal-exit.patch")
    assert "Received second interrupt, terminating immediately." in " ".join(ISSUES["B19"].split())
    assert "Received second interrupt, terminating immediately." in second and "std::_Exit(1)" in second
    assert "std::_Exit(1)" in ISSUES["B19"] and "15.06 s" in ISSUES["B19"] and "15.06 s" in second
    # B24: the function it names is the one the patch changes
    hang = patch("0029-watchdog-settings-update-hang.patch")
    assert "WaitDone" in ISSUES["B24"] and "func (wd *WatchDog) WaitDone() {" in hang
    assert "atomic.Bool" in ISSUES["B24"] and re.search(r"started\s+atomic\.Bool", hang)
    # B20: the owner lock is what the patch does
    leases = patch("0019-voiceprofile-abandoned-leases.patch")
    assert "LockFileEx" in ISSUES["B20"] and "LockFileEx" in leases and ".leases" in ISSUES["B20"]
    # B17: the refusal's words and the function, as the opt-in patch has them
    opt_in = patch("0011-config-voice-cloning-opt-in-unknown-backend.patch")
    assert "VoiceCloningForModel" in ISSUES["B17"] and "VoiceCloningForModel" in opt_in
    # B18: the type that must be neither kind of exception, as the second patch says
    pool = patch("0018-llama-cpp-sycl-pool-refusal-reaches-the-graph.patch")
    assert "GGML_STATUS_ALLOC_FAILED" in ISSUES["B18"] and "static_assert" in pool


def test_the_index_says_what_is_written_and_what_upstream_already_has():
    readme = (REPORTS / "README.md").read_text(encoding="utf-8")
    assert "**B17, B19, B20, B21, B22, B24, B25, B26 written out to be filed as they are**" in readme
    assert "Nothing here has been filed yet." in readme
    for near in ("LocalAI #11970", "LocalAI #12204 and #12425", "llama.cpp #29581", "llama.cpp #27595",
                 "Nothing found for B20, B22, B24, B25, or B17's request."):
        assert near in readme, near
    # an issue that leans on an upstream thread names it
    assert "#11970" in ISSUES["B26"] and "#12425" in ISSUES["B21"] and "#12204" in ISSUES["B21"]
    assert "ggml-org/llama.cpp #29581" in ISSUES["B19"] and "#27595" in ISSUES["B18"]
