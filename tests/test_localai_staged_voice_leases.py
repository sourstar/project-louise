"""Staged 0019, a patch of LocalAI's SERVER: the request leases of a process
that is gone do not keep a deleted profile's recording on disk
(docs/notes/localai-bugs.md B20 / P19; seen on the node 2026-11-25).

LocalAI's own Go suite is the patch's test and ran where the patch was
written (25 specs, 6 new). Held here: what the patch is and is not — a
server patch in no engine series, so it renames no engine; the lock that
tells a dead process from a living one; where the purge runs; and that the
note says what it does on the node the first time it starts."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
STAGED = PATCHES / "staged"
PATCH = STAGED / "0019-voiceprofile-abandoned-leases.patch"
BUGS = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")


def _files() -> dict[str, str]:
    text = PATCH.read_text(encoding="utf-8")
    parts = re.split(r"^diff --git a/(\S+) b/\S+\n", text[text.index("\n---\n"):], flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def _added(diff: str) -> str:
    return "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))


def test_it_is_a_mail_under_localai_s_policy_and_touches_only_the_voice_profile_store():
    text = PATCH.read_text(encoding="utf-8")
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert set(_files()) == {f"core/services/voiceprofile/{name}" for name in (
        "store.go", "lease_owner.go", "lease_owner_unix.go", "lease_owner_windows.go",
        "lease_owner_test.go", "export_test.go")}


def test_it_is_a_server_patch_and_renames_no_engine():
    """An engine's name is the digest of its series and patches: a server patch in an engine series would cost a
    build and every gate for code the engine does not have."""
    for series in (PATCHES / "series", *STAGED.glob("*.series")):
        assert PATCH.name not in series.read_text(encoding="utf-8"), series.name
    assert not any("backend/cpp/" in path for path in _files())


def test_a_dead_process_is_told_from_a_living_one_by_a_lock_the_kernel_drops():
    files = _files()
    owner = _added(files["core/services/voiceprofile/lease_owner.go"])
    assert 'leaseOwnerFileName = ".owner"' in owner
    # the owner file is opened inside the store's root and locked for the Store's life
    assert "root.OpenFile(filepath.Join(s.leaseDir, leaseOwnerFileName), os.O_CREATE|os.O_RDWR, 0o600)" in owner
    assert "s.leaseOwner = owner" in owner
    unix = _added(files["core/services/voiceprofile/lease_owner_unix.go"])
    assert "//go:build !windows" in unix and "syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)" in unix
    assert "errors.Is(err, syscall.EWOULDBLOCK)" in unix, "held by a living process: not an error, not abandoned"
    assert "windows.LockFileEx(" in _added(files["core/services/voiceprofile/lease_owner_windows.go"])
    # only real directories, never this Store's own; a directory without an owner file by its own age
    assert "if !entry.Type().IsDir() || entry.Name() == own {" in owner
    assert "abandonedLeaseAge = 24 * time.Hour" in owner
    assert "s.now().Sub(info.ModTime()) > abandonedLeaseAge" in owner


def test_the_purge_runs_at_start_and_at_every_delete_and_close_lets_go_last():
    store = _files()["core/services/voiceprofile/store.go"]
    added = _added(store)
    assert added.count("s.purgeAbandonedLeases(root)") == 2
    assert re.search(r"if err := s\.claimLeaseDirectory\(root\); err != nil \{.*?\}\n\s+// what the processes before this one "
                     r"left behind\n\s+s\.purgeAbandonedLeases\(root\)", added, re.S), "at start, once the Store owns its own"
    assert re.search(r"^ \tif err := root\.RemoveAll\(id\); err != nil \{\n.*?\n \t\}\n\+\ts\.purgeAbandonedLeases\(root\)\n",
                     store, re.M | re.S), "after the profile's own directory is gone"
    assert "_ = s.leaseOwner.Close()" in added
    tests = _added(_files()["core/services/voiceprofile/lease_owner_test.go"])
    for spec in ("leaves nothing of a deleted recording behind when the process that leased it was killed",
                 "keeps the lease of a living store through a delete, as a synthesis in flight needs",
                 "removes at start what the processes before it left behind",
                 "removes a lease directory without an owner file only once it is old",
                 "follows no link and removes no stray file under the lease directory",
                 "locks an owner file while it lives and leaves none when it closes"):
        assert f'It("{spec}"' in tests, spec


def test_the_note_says_what_was_seen_what_it_does_on_the_node_and_what_stands_until_then():
    assert "| B20 | defect |" in BUGS and "| **P19 (staged)** | **LIVE in the node's SERVER since 2026-11-25" in BUGS
    section = BUGS[BUGS.index("### B20 and P19 (staged)."):BUGS.index("### B19 and P15 (staged).")]
    assert "1,331,940 bytes, link count 2" in section and "Nothing of the deleted test profile remained" in section
    assert "It takes effect when the SERVER is rebuilt with it" in section
    assert "is the operator's word" in section, "its first start deletes nine directories on the node"
    assert "find … -samefile" in section, "the rule that stands until it is live"
    readme = (STAGED / "README.md").read_text(encoding="utf-8")
    assert f"`{PATCH.name}`" in readme and "a patch of NO engine series" in readme
    # and what it did on the node the first time it started, and the three layers after a delete
    assert "seven lines \"Removed the voice profile leases of a process that is\ngone\"" in section
    assert "no file\nof its clip's inode anywhere under `voice-profiles`" in section
