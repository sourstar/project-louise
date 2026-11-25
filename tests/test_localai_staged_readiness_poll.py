"""Parking, step 2, the SERVER part's first commit, separable — STAGED
(docs/notes/parking-step-2.md §3.2 "The readiness poll"; operator, 2026-11-24:
"Patch localAI when clockwork is implemented"; the architecture session,
2026-11-25: "accepted as written; it goes on the node with the server part").

LocalAI asks a backend it just started whether it is ready, sleeps 2 s, asks
again: a backend ready after 100 ms is seen two seconds later, on every cold
load. The patch asks every 50 ms inside the same wait. Its Go tests ran where
it was written (the whole pkg/model suite, on v5.0.1 + P1 + P13 + P19). Held
here: that the wait is the same wait — a model's own attempts and delay mean
what they meant — and that it is a patch of LocalAI's SERVER in no engine
series."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
PATCH = PATCHES / "staged" / "0024-model-readiness-poll.patch"
NOTE = (ROOT / "docs" / "notes" / "parking-step-2.md").read_text(encoding="utf-8")


def _text() -> str:
    return PATCH.read_text(encoding="utf-8")


def _added() -> str:
    text = _text()
    return "\n".join(line[1:] for line in text[text.index("\n---\n"):].splitlines()
                     if line.startswith("+") and not line.startswith("+++"))


def _removed() -> str:
    text = _text()
    return "\n".join(line[1:] for line in text[text.index("\n---\n"):].splitlines()
                     if line.startswith("-") and not line.startswith("---"))


def test_it_is_a_mail_under_localai_s_policy_for_the_server_alone():
    text = _text()
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    files = re.findall(r"^diff --git a/(\S+)", text, re.M)
    assert files == ["pkg/model/initializers.go", "pkg/model/initializers_readiness_test.go"]
    assert not any(f.startswith("backend/") for f in files), "the server's Go: no engine is rebuilt for it"


def test_it_is_staged_for_the_server_and_in_no_engine_series():
    for series in (PATCHES / "series", *(PATCHES / "staged").glob("*.series")):
        assert "readiness-poll" not in series.read_text(encoding="utf-8"), series.name
    readme = (PATCHES / "staged" / "README.md").read_text(encoding="utf-8")
    row = next(line for line in readme.splitlines() if line.startswith("| `" + PATCH.name + "`"))
    assert "SERVER" in row and "NO engine series" in row


def test_the_wait_is_the_same_wait_asked_more_often():
    added, removed = _added(), _removed()
    assert "const readinessPollInterval = 50 * time.Millisecond" in added
    # the old loop: one check, then the whole delay
    assert "time.Sleep(time.Duration(o.grpcAttemptsDelay) * time.Second)" in removed
    # the budget is what the configuration gave: attempts x delay, spent sleeping
    assert "budget := time.Duration(attempts) * delay" in added
    assert "interval := min(readinessPollInterval, delay)" in added, "a delay under the interval is slept as it is"
    assert re.search(r"if slept >= budget \{\n\s+return false, err\n\s+\}\n\s+step := min\(interval, budget-slept\)", added)
    # the caller passes the model's own two numbers, unchanged in meaning
    assert "}, o.grpcAttempts, time.Duration(o.grpcAttemptsDelay)*time.Second, time.Sleep)" in added
    # with no delay: as many checks as before, back to back
    assert re.search(r"if delay <= 0 \{.*?for range attempts \{", added, re.S)
    assert "if attempts <= 0 {\n\t\treturn false, nil" in added


def test_its_own_tests_hold_both_ends_of_the_wait():
    added = _added()
    assert 'Expect(total()).To(Equal(40 * time.Second))' in added, "never ready: no more and no less than 20 x 2 s"
    assert "Expect(slept).To(Equal([]time.Duration{50 * time.Millisecond, 50 * time.Millisecond, 50 * time.Millisecond}))" in added
    assert "Expect(slept).To(BeEmpty())" in added, "ready at the first check: no sleep at all"
    assert len(re.findall(r"^\tIt\(", added, re.M)) == 7


def test_the_note_says_it_is_the_first_commit_and_what_is_not_measured():
    section = NOTE[NOTE.index("## 7. The server part"):]
    for said in ("0024-model-readiness-poll.patch", "v5.0.1 + P1 + P13 + P19", "NOT measured", "cold load"):
        assert said in section, said
