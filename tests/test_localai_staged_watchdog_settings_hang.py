"""Staged 0029: a settings update no longer hangs on a watchdog whose loop never
ran (docs/notes/localai-bugs.md B24; parking-step-2.md §8).

LocalAI's own defect, v5.0.1, met on a real instance while parking's rows were
run: POST /api/settings restarts the watchdog and waits for its loop to end,
and the loop is only started where a periodic check is on. With a limit of
loaded models and no check, the write never answered and held the lock every
later write needs. Louise's keeper writes settings (the reclaimer's switch,
the parking floor), so the node must not meet it.

Held here: what the patch changes and nothing else; that it is the SERVER's, in
no engine series, after 0027; and what the notes say of the node."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
PATCH = PATCHES / "staged" / "0029-watchdog-settings-update-hang.patch"
BUGS = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")
NOTE = (ROOT / "docs" / "notes" / "parking-step-2.md").read_text(encoding="utf-8")


def _added(text: str) -> str:
    return "\n".join(line[1:] for line in text[text.index("\n---\n"):].splitlines()
                     if line.startswith("+") and not line.startswith("+++"))


def test_it_is_a_mail_under_localai_s_policy_for_the_watchdog_alone():
    text = PATCH.read_text(encoding="utf-8")
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert re.findall(r"^diff --git a/(\S+)", text, re.M) == ["pkg/model/watchdog.go",
                                                              "pkg/model/watchdog_waitdone_internal_test.go"]
    assert "no answer in 300 s" in message and "The settings themselves had been applied" in message


def test_a_loop_that_never_ran_is_not_waited_for_and_one_that_runs_still_is():
    added = _added(PATCH.read_text(encoding="utf-8"))
    assert re.search(r"started\s+atomic\.Bool", added) and "wd.started.Store(true)" in added
    text = PATCH.read_text(encoding="utf-8")
    # the wait itself stays, below the new test: a loop that runs is waited for as before
    assert re.search(r"^\+\tif !wd\.started\.Load\(\) \{\n\+\t\treturn\n\+\t\}\n \t<-wd\.done", text, re.M)
    # its three cases are specs of the patch
    for case in ("does not wait for a loop that was never started", "still waits for a loop that runs",
                 "returns for a loop that ended by itself because no check was on"):
        assert case in added, case


def test_it_is_staged_for_the_server_after_the_park_patch_and_in_no_engine_series():
    for series in (PATCHES / "series", *(PATCHES / "staged").glob("*.series")):
        assert "watchdog-settings-update-hang" not in series.read_text(encoding="utf-8"), series.name
    readme = (PATCHES / "staged" / "README.md").read_text(encoding="utf-8")
    row = next(line for line in readme.splitlines() if line.startswith(f"| `{PATCH.name}` |"))
    assert "SERVER" in row and "NO engine series" in row and "after 0027" in row
    assert "e37b86e089b38d2667de8368965eddd1e6bf0782" in NOTE, "the tree the server build must be, after it"


def test_the_notes_say_what_was_seen_and_that_the_node_is_not_in_the_combination():
    row = next(line for line in BUGS.splitlines() if line.startswith("| B24 | defect |"))
    for said in ("POST /api/settings", "WaitDone", "max_active_backends", "300 s", "staged 0029",
                 "busy check on, no limit", "to file upstream"):
        assert said in row, said
    section = NOTE[NOTE.index("## 8. "):]
    assert "THE NODE IS NOT IN THAT COMBINATION" in section and "must not be put in it before 0029 is in its server" in section
    assert "The same write: 0.09 s" in section
