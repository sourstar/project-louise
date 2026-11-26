"""Staged 0031: a park refused for the host's memory floor says what it read
(docs/notes/parking-step-2.md §14; the architecture session, 2026-11-26: "log
0027's 412 body ... so a refusal names its numbers next time").

Held here: that it is the SERVER's, in no engine series, after 0029; that the
park is decided on one reading by the rule it had; that the refusal is logged
with every figure and its answer ends with the reading; that the ZFS ARC is
said and decided on by nobody; and what the notes say was and was not shown."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
PATCH = PATCHES / "staged" / "0031-model-park-floor-refusal-says-what-it-read.patch"
NOTE = (ROOT / "docs" / "notes" / "parking-step-2.md").read_text(encoding="utf-8")
TREE = "ab5de7ade4dbfbccff216ea614adb65f6c98bbf2"


def _added(text: str) -> str:
    return "\n".join(line[1:] for line in text[text.index("\n---\n"):].splitlines()
                     if line.startswith("+") and not line.startswith("+++"))


def _removed(text: str) -> str:
    return "\n".join(line[1:] for line in text[text.index("\n---\n"):].splitlines()
                     if line.startswith("-") and not line.startswith("---"))


def test_it_is_a_mail_under_localai_s_policy_for_the_server_alone():
    text = PATCH.read_text(encoding="utf-8")
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert re.findall(r"^diff --git a/(\S+)", text, re.M) == [
        "pkg/model/park.go", "pkg/model/park_internal_test.go",
        "pkg/xsysinfo/memory_headroom.go", "pkg/xsysinfo/memory_headroom_test.go"]
    for series in (PATCHES / "series", *(PATCHES / "staged").glob("*.series")):
        assert PATCH.name not in series.read_text(encoding="utf-8"), series.name


def test_the_park_is_decided_on_one_reading_by_the_rule_it_had():
    text = PATCH.read_text(encoding="utf-8")
    added, removed = _added(text), _removed(text)
    assert "reading := readHostMemory()" in added and "free, saw = reading.Allocatable(), reading.String()" in added
    assert "var readHostMemory = xsysinfo.ReadMemory" in added
    assert "return xsysinfo.AllocatableMemory(), nil" in removed
    # the rule: the smaller of MemAvailable and each limited cgroup's room - unchanged, and one function for both
    assert "return ReadMemory().Allocatable()" in added
    rule = added[added.index("func (r MemoryReading) Allocatable() uint64 {"):]
    rule = rule[:rule.index("\n}")]
    assert "best := r.HostAvailable" in rule and "if c.Limited && c.Headroom < best {" in rule
    assert "ARC" not in rule, "the ARC is said and decided on by nobody"
    assert "return readingFrom(hostAvailable, v2Max, v2Current, v2Stat, v1Limit, v1Usage, v1Stat, \"\").Allocatable()" in added
    # the fit itself is not touched
    assert "fits := free >= reserved && free-reserved >= size && free-reserved-size >= floor" not in removed


def test_a_refusal_is_logged_with_every_figure_and_its_answer_ends_with_the_reading():
    added = _added(PATCH.read_text(encoding="utf-8"))
    log = added[added.index('xlog.Warn("A park was refused: it would leave the host under its memory floor"'):]
    log = log[:log.index("return ParkedState{}")]
    for field in ('"model", modelName', '"available_mib", free>>20', '"parking_by_others_mib", reserved>>20',
                  '"to_park_mib", size>>20', '"floor_mib", floor>>20', '"read", saw'):
        assert field in log, field
    assert "a floor of %d MiB (%s)\",\n\t\t\tErrParkHostFloor, free>>20, reserved>>20, size>>20, floor>>20, saw)" in added
    for said in ('"MemAvailable %d MiB"', '"; %s: limit %d MiB, working set %d MiB, room %d MiB"',
                 '"; ZFS ARC %d MiB, its floor %d MiB (not counted in MemAvailable)"', '"; no ZFS ARC"',
                 'zfsArcstatsPath = "/proc/spl/kstat/zfs/arcstats"', 'case "size":', 'case "c_min":'):
        assert said in added, said
    # the spec that fails on the old code: one read, the reading named
    assert 'Expect(reads).To(Equal(1), "read once")' in added
    assert "(MemAvailable 40960 MiB; cgroup v2: limit 24576 MiB, working set" in added


def test_the_notes_give_the_tree_and_say_what_was_shown_and_what_was_not():
    section = NOTE[NOTE.index("## 14. A refusal for the host's floor says what it read (2026-11-26)"):]
    for said in (TREE, "21:57:32Z", "12,884,901,888", "**What it does not do: count the ARC.**", "Pros of counting it",
                 "Cons:", "| the log line on the node | NOT measured", "279 of 279", "695 of 742"):
        assert said in section, said
    readme = (PATCHES / "staged" / "README.md").read_text(encoding="utf-8")
    assert f"**`ab5de7ad…` after it** (`{TREE}`)" in readme
    bugs = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")
    assert next(line for line in bugs.splitlines() if line.startswith("| **P29 (staged)** |"))
