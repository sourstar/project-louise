"""EUR-Lex units built from the DATA DUMP ALONE (operator, 2026-11-15, B:
"Build every in-force act, in all 24 languages, from the Data Dump alone";
CLAUDE.md, bulk first: "until the bulk download is not fully used no cherry
picking is allowed. No exceptions").

WHAT A UNIT IS (parser 4, app/plugins/eurlex/source.py): `lang=<l>/<yyyy>/
<CELEX>/` holding the items of the act's best form and `tree.json` — the
work's non-inferred tree scoped to the language. The dump holds both: each
work's tree in LEG_MTD.zip (`<work uuid>/tree_non_inferred.rdf`, the graph
Cellar serves as `notice=non-inferred-tree`; München's units: the two scoped
trees are one canonical JSON) and its items in the language's zips
(`<work uuid>/<form>/<name>`, FMX, HTML and PDF). No Cellar request is made.

THE UNIT IS THE ONE CELLAR WOULD GIVE, or it is not built here. The form is
the one a fetch from Cellar chooses on the same tree (`cellar.form_of`: the
best form the tree lists, a wrapper-less Formex giving way), so
`records-vs-origin`, which re-fetches from Cellar
(`cellar.fetch_unit_from_tree`), reproduces it byte for byte. A work whose
chosen form the dump does not hold whole is left to the API (`incomplete`),
never built from another form. Every item is read with its zip CRC-32 checked;
a Formex wrapper stating another language is refused, as a fetch refuses it.

WHAT IT NEVER DOES: overwrite a held unit (base or history) — a resumed pass
skips what landed; move a unit; ask the network. A unit lands whole or not at
all (cellar.land_many: staged, synced by group commit, swapped).

THE HISTORY SLICE (operator, 2026-11-15: "Add history as a download and
embedding option"). A unit built from the dump is the base, `lang=<l>/…`.
`carry` takes the units an earlier per-unit fetch landed (parser 3: notice,
tree, items) that the dump did not give, keeps their items and tree — the
same bytes (RULE 1) — and drops the notice, which parser 4 reads for nothing.
Their place: `history/lang=<l>/…` (a consolidated version, or an act its tree
does not state in force) or the base (an act in force the dump lacked: one
modified since, or new). The place is fixed when the unit is first held and
never moved, so the slice is a pure function of the path.

MULTI-PROCESS (CLAUDE.md): the works in chunks over the parse pool
(`parallel.each_part`); each child opens the MTD and one language's zips at a
time (staged on the files cache, derived.staged), so a chunk is one tree
parse per work and one zip lookup per item.

THE STANDARD, NOT ADOPTED: none for "a bulk as the units of a per-unit
origin"; the mapping is the dump's layout, written in datadump.py.
"""
from __future__ import annotations

import os
import re
import threading
import zipfile
from collections import Counter, OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from .spec import HeldBack, TransferAborted

#: works per task: a chunk parses its trees once and visits every language.
#: MEASURED (2026-11-15): a work's MTD tree is 67-390 KB of XML, 0.24-1.4 MB
#: parsed (13-86 ms); one language's zips index in ~1.4 s and ~250 MB (FMX's
#: 122 k members alone 0.78 s). A task holds its chunk's trees and one
#: language at a time, about 0.4 GB, and pays ~34 s to open the 24 languages
#: (59 k works: ~300 tasks, ~20 min over 8 children); parsing each tree once
#: per language instead would cost 24 times its ~50 min
CHUNK = 200
#: a batch of units landed by one group commit (cellar.land_many): at most
#: this many, or this many bytes, held in a child at once
LAND_BATCH = 64
LAND_BYTES = 64 << 20
#: the corpus root's record of the dump built into it: `{lang: its stated
#: creation}`, written when a language's build has visited every work. A
#: parser-4 collection asks Cellar nothing per unit before it names every
#: language it holds (cellar.CellarInventory.held_back: bulk first)
STAMP = ".eurlex-dump.json"
#: one language's zips held open per child (their central directories,
#: ~250 MB): a task visits the languages in turn, so a second would never be
#: asked for again before it is evicted
OPEN_LANGUAGES = 1
_MEMBER = re.compile(r"^(?P<uuid>[0-9a-f-]{36})/(?P<form>[a-z0-9]+)/(?P<name>[^/]+)$")
_MTD_MEMBER = re.compile(r"^(?P<uuid>[0-9a-f-]{36})/tree_non_inferred\.rdf$")
_LG_DOC = re.compile(rb"<LG\.DOC>\s*([A-Z]{2})\s*</LG\.DOC>")


class BulkUnreadable(HeldBack):
    """A WHOLE zip of the dump that does not open (still being copied, cut:
    its central directory unread). BULK FIRST (review 2026-11-21, Q6): the
    build stops — nothing stamped, nothing recorded for the API — and the
    pass is held back, said why (spec.HeldBack). Until then an unreadable
    newer MTD sent the whole pass to Cellar with the bulk unused, and an
    unreadable item zip was skipped "left to the API" with its language
    stamped built: its units never in the dump's record, asked of Cellar by
    the listing. A single MEMBER that does not read is not this
    (MemberUnreadable)."""


class MemberUnreadable(Exception):
    """One member of a zip that opens does not read (a bad CRC-32, a cut
    member): a fact about ITS unit — the bulk cannot give it — so the unit
    is the API's, recorded with this reason, and the build goes on
    (coordinator, 2026-11-21: holding the pass for it stalled EUR-Lex on
    every pass for one corrupt member)."""


def _unreadable(p, e: BaseException) -> BulkUnreadable:
    return BulkUnreadable(f"the Data Dump's {p} does not read ({type(e).__name__}: {e}): bulk first, so its "
                          f"build stops and Cellar is not asked for its units until it reads")


class LangZips:
    """One language's item zips, indexed `uuid → form → name → (zip, info)`.
    A zip that does not open raises BulkUnreadable (Q6)."""

    def __init__(self, folder: Path, log=print) -> None:
        from .datadump import _staged
        self.zips: list[zipfile.ZipFile] = []
        self.paths: list[Path] = []
        self.index: dict[str, dict[str, dict[str, tuple[int, zipfile.ZipInfo]]]] = {}
        for p in sorted(folder.rglob("*.zip")):
            if "MTD" in p.name.upper():
                continue
            try:
                # the PDF zips (~22 GB a language, 530 GB in all) are read in
                # place: their members are asked for only by the few acts with
                # no Formex and no HTML, and staging one would fill the cache
                zf = zipfile.ZipFile(p if "PDF" in p.name.upper() else _staged(p, log))
                infos = zf.infolist()
            except (zipfile.BadZipFile, OSError) as e:
                self.close()
                raise _unreadable(p, e) from e
            i = len(self.zips)
            self.zips.append(zf)
            self.paths.append(p)
            for info in infos:
                m = _MEMBER.match(info.filename)
                if m:
                    self.index.setdefault(m["uuid"], {}).setdefault(m["form"], {})[m["name"]] = (i, info)

    def read(self, ref: tuple[int, zipfile.ZipInfo]) -> bytes:
        """The member's bytes, its CRC-32 checked; one that does not read
        raises MemberUnreadable naming the zip, the member and the error."""
        i, info = ref
        try:
            return self.zips[i].read(info)
        except (zipfile.BadZipFile, OSError, EOFError) as e:
            raise MemberUnreadable(f"{self.paths[i]} member {info.filename}: {type(e).__name__}: "
                                   f"{e}"[:400]) from e

    def close(self) -> None:
        for z in self.zips:
            z.close()


_LANGS: OrderedDict[str, LangZips] = OrderedDict()
_MTDS: dict[tuple[str, ...], list[zipfile.ZipFile]] = {}


def _lang(folder: str) -> LangZips:
    got = _LANGS.get(folder)
    if got is None:
        got = _LANGS[folder] = LangZips(Path(folder))
        while len(_LANGS) > OPEN_LANGUAGES:
            _LANGS.popitem(last=False)[1].close()
    _LANGS.move_to_end(folder)
    return got


def _mtd(paths: tuple[str, ...]) -> list[zipfile.ZipFile]:
    from .datadump import _staged
    got = _MTDS.get(paths)
    if got is None:
        got = []
        for p in paths:
            try:
                got.append(zipfile.ZipFile(_staged(Path(p), print)))
            except (zipfile.BadZipFile, OSError) as e:
                for z in got:
                    z.close()
                raise _unreadable(p, e) from e
        _MTDS[paths] = got
    return got


def mtd_zips(folders: list[Path]) -> list[Path]:
    """The MTD zips under the language folders, each once (LEG_MTD.zip is
    linked into every folder)."""
    seen: dict[Path, Path] = {}
    for folder in folders:
        for p in sorted(folder.rglob("*.zip")):
            if "MTD" in p.name.upper():
                seen.setdefault(p.resolve(), p)
    return sorted(seen.values())


def works(mtd: list[Path]) -> list[str]:
    """The works the MTD zips hold; one that does not open raises
    BulkUnreadable (Q6: it raised into `prepare`, whose failure the engine
    took for "not prepared" and asked Cellar for every unit)."""
    out: set[str] = set()
    for p in mtd:
        try:
            with zipfile.ZipFile(p) as z:
                out.update(m["uuid"] for m in map(_MTD_MEMBER.match, z.namelist()) if m)
        except (zipfile.BadZipFile, OSError) as e:
            raise _unreadable(p, e) from e
    return sorted(out)


def _held(corpus: Path, rel: str) -> bool:
    from ..plugins.eurlex import rdf
    from ..plugins.eurlex.source import HISTORY
    return any((base / rel / rdf.TREE_FILE).is_file() for base in (corpus, corpus / HISTORY))


def _work_celex(tree: dict, uuid: str) -> str | None:
    """The CELEX of the work whose cellar URI is `uuid`'s."""
    from ..plugins.eurlex import from_tree, rdf
    find, gp = rdf.groups(tree)
    me = find(f"http://publications.europa.eu/resource/cellar/{uuid}")
    props = gp.get(me) or {}
    got = from_tree._vals(props, "resource_legal_id_celex")
    return got[0] if got else None


def stamp(corpus: Path) -> dict[str, str]:
    """`{lang: the creation of the dump built into the corpus}`."""
    import json
    try:
        got = json.loads((Path(corpus) / STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else {}


def _stamp(corpus: Path, langs: dict[str, str]) -> None:
    import json
    path = Path(corpus) / STAMP
    now = {**stamp(corpus), **langs}
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(now, sort_keys=True, indent=1), encoding="utf-8")
    os.replace(tmp, path)


#: A DUMP FOLDER WHOSE STATEMENT DOES NOT READ is never skipped silently
#: (coordinator, 2026-11-21): `Dumps._scan` printed it and went on, and a
#: worker's pass said nothing. Said as a warning of the collection at most
#: this often per folder (`unread_statements`, through TreeInventory.reports)
UNREAD_SAY_S = 24 * 3600.0
#: (corpus, folder) -> when it was last said; the process's
_unread_said: dict[tuple[str, str], float] = {}


def _latest(folder: Path) -> float:
    """A dump folder's latest change: its own, its statement's, its zips'."""
    times = []
    for p in [folder, folder / "datadump.json", *folder.rglob("*.zip")]:
        try:
            times.append(p.stat().st_mtime)
        except OSError:
            continue
    return max(times, default=0.0)


def unread_statements(dump_dir: Path, corpus: Path, *, now: float | None = None) -> list[str]:
    """One warning for each dump folder under `dump_dir` whose datadump.json
    does not read (missing beside its zips, cut, not its form), not said for
    this corpus within UNREAD_SAY_S. It holds nothing back: the dumps that
    read are a bulk still, used as before. Where the folder is the NEWEST —
    changed after every folder that reads, by its files' times, since its
    own creation is what does not read — the warning says the newer dump is
    not used and why."""
    import time as _time
    from .datadump import STATEMENT, statement
    base = Path(dump_dir)
    if not base.is_dir():
        return []
    now = _time.time() if now is None else now
    from .datadump import dump_folders
    folders = dump_folders(base)
    read = {p: statement(p) for p in folders}
    newest_read = max((_latest(p) for p, got in read.items() if not isinstance(got, str)), default=None)
    out = []
    for folder, got in read.items():
        if not isinstance(got, str):
            continue
        key = (str(Path(corpus).resolve()), str(folder.resolve()))
        if now - _unread_said.get(key, float("-inf")) < UNREAD_SAY_S:
            continue
        _unread_said[key] = now
        if newest_read is None or _latest(folder) > newest_read:
            out.append(f"the newest Data Dump folder is not used: {got}; the dumps built in stand, and "
                       f"Cellar is asked only for what they lack")
        else:
            out.append(f"a Data Dump folder is not used: {got}; the dumps that read are used as before")
    return out


#: the languages whose units a build has re-formed from the dump where a
#: held unit lacked its Formex package (2026-11-19): after it, the per-unit
#: pass may ask Cellar for what still lacks one (cellar.TreeUnits._reform)
PACKAGES = ".eurlex-dump-packages.json"


def packages_read(corpus: Path) -> set[str]:
    import json
    try:
        got = json.loads((Path(corpus) / PACKAGES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(x) for x in got} if isinstance(got, list) else set()


def _mark_packages(corpus: Path, langs: list[str]) -> None:
    import json
    path = Path(corpus) / PACKAGES
    now = sorted(packages_read(corpus) | set(langs))
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(now), encoding="utf-8")
    os.replace(tmp, path)


#: the units the dump lists and does not hold whole (`incomplete`): `{lang:
#: {celex: {"in_force": …, "tried": null | "<why it failed>"}}}`, written by
#: the build for every language it visited. The per-unit pass asks Cellar
#: for exactly these FIRST (cellar.TreeInventory: no listing), then lists
#: (München, 2026-11-16: ~10.5k units waited behind a 24-language listing
#: of every year since 1952)
INCOMPLETE = ".eurlex-dump-incomplete.json"
#: THE RECORD'S LOCK ACROSS PROCESSES (review 2026-11-21, Q4): the CLI build
#: (`louise dev eurlex-dump`) and the worker's per-unit pass both read, change
#: and write the record; a lock per process lost the other's marks (300 marks
#: against 40 builds: 2 marks and 7 of the build's 40 lost). flock(2) on a
#: file beside it — the standard advisory lock, released by the kernel when
#: its holder dies, so a killed process leaves no stale lock; Linux, the
#: node's. The thread lock beside it: one holder per process at a time.
LOCK = ".eurlex-dump-incomplete.lock"
_incomplete_lock = threading.Lock()


@contextmanager
def _locked(corpus: Path):
    """The record held for a read-modify-write, against every thread and
    process. Never nested (a second open of the lock is a second lock)."""
    import fcntl
    path = Path(corpus) / LOCK
    with _incomplete_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)                    # the lock with it


#: THE MARKS SINCE THE RECORD WAS WRITTEN (review 2026-11-21, Q5): one JSON
#: line a mark — `fetched`, `tried`, the state of a unit to re-form — appended
#: under the lock and folded into the record when the build writes it, or
#: once the marks outgrow it (`_mark`). Until then every mark rewrote the
#: whole record: 15.6 MiB at 240k entries, 379 ms under the lock per unit,
#: ~11 min an hour serialized and ~26 GiB an hour written at 1,695 units an
#: hour. Its first line names the record it marks (inode, mtime, size): marks
#: written against a record since replaced were folded into it, or belong to
#: a build that replaced them, and are read no more. A mark lost to a crash
#: (no fsync: bookkeeping, not data) costs one more request for its unit.
#: THE STANDARD, NOT ADOPTED: this is a write-ahead log; SQLite (WAL) would
#: hold it as a table, at the cost of a schema beside the manifest's for one
#: record the build rewrites whole
MARKS = ".eurlex-dump-incomplete.marks"
#: the marks are folded once they are larger than the record (and this):
#: each fold rewrites the record after at least as many bytes of marks, so a
#: mark costs O(1) amortised, and a read at most twice the record
FOLD_MIN = 1 << 20


def _sig(path: Path) -> list[int] | None:
    """A file's identity as a stat gives it: (inode, mtime ns, size)."""
    try:
        st = path.stat()
    except OSError:
        return None
    return [st.st_ino, st.st_mtime_ns, st.st_size]


def _read_record(corpus: Path) -> dict:
    import json
    try:
        got = json.loads((Path(corpus) / INCOMPLETE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _read_marks(corpus: Path, record: list[int] | None) -> list[dict]:
    """The marks written against the record `record` names (`_sig`)."""
    import json
    try:
        lines = (Path(corpus) / MARKS).read_bytes().splitlines()
    except OSError:
        return []
    try:
        head = json.loads(lines[0]) if lines else None
    except ValueError:
        return []
    if record is None or not isinstance(head, dict) or head.get("record") != record:
        return []
    out = []
    for line in lines[1:]:
        try:
            m = json.loads(line)
        except ValueError:
            continue                        # a line a crash cut
        if isinstance(m, dict):
            out.append(m)
    return out


def _fold(record: dict, marks: list[dict]) -> dict:
    """The marks applied in order to the entries they name; a unit the record
    no longer names (recorded afresh without it) takes none."""
    for m in marks:
        entry = (record.get(m.get("l")) or {}).get(m.get("c"))
        if not isinstance(entry, dict):
            continue
        kind, value = m.get("k"), m.get("v")
        if kind == "fetched":
            entry["fetched"] = entry.get("fetched") or value      # the first time it was
        elif kind == "tried":
            entry["tried"] = value
        elif kind == "state" and isinstance(value, dict):
            entry.update({k: value[k] for k in ("held", "lacks", "whole") if k in value})
        elif kind == "listed":
            entry.pop("waiting", None)           # its window came back listed: due at once, n from 0
        elif kind == "waiting" and isinstance(value, dict):
            # waiting for its LISTING window, apart from Cellar's episodes on it,
            # which keep their own count (review of d863230c, Y2)
            prev = entry.get("waiting") if isinstance(entry.get("waiting"), dict) else {}
            entry["waiting"] = {"at": value.get("at"), "n": int(prev.get("n") or 0) + 1, "why": value.get("why")}
        elif kind == "deferred" and isinstance(value, dict):
            prev = entry.get("deferred") if isinstance(entry.get("deferred"), dict) else {}
            entry["deferred"] = {"at": value.get("at"), "n": int(prev.get("n") or 0) + 1, "why": value.get("why")}
    return record


def _current(corpus: Path) -> dict:
    """The record with its marks; under the lock."""
    corpus = Path(corpus)
    return _fold(_read_record(corpus), _read_marks(corpus, _sig(corpus / INCOMPLETE)))


def incomplete(corpus: Path) -> dict[str, dict[str, dict]]:
    """The record as it stands: as the build wrote it, with every mark since."""
    corpus = Path(corpus)
    if not (corpus / INCOMPLETE).is_file():
        return {}
    with _locked(corpus):
        return _current(corpus)


def _write_incomplete(corpus: Path, data: dict) -> None:
    """The record written whole, its marks folded into `data`; under the lock."""
    import json
    path = Path(corpus) / INCOMPLETE
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    # folded: read no more (its first line names the record just replaced)
    (Path(corpus) / MARKS).unlink(missing_ok=True)


def _mark(corpus: Path, marks: list[tuple[str, str, str, Any]]) -> None:
    """`(lang, celex, kind, value)` marks appended to the record's in one
    write: O(1) a mark, whatever the record's size. Nothing where no record
    is (no build recorded one)."""
    import json
    corpus = Path(corpus)
    if not marks or not (corpus / INCOMPLETE).is_file():
        return
    with _locked(corpus):
        record = _sig(corpus / INCOMPLETE)
        if record is None:
            return
        text = "".join(json.dumps({"l": l, "c": c, "k": k, "v": v}, sort_keys=True, separators=(",", ":"))
                       + "\n" for l, c, k, v in marks)
        with open(corpus / MARKS, "a+b") as f:
            f.seek(0)
            try:
                head = json.loads(f.readline() or b"null")
            except ValueError:
                head = None
            if not isinstance(head, dict) or head.get("record") != record:
                f.truncate(0)               # marks of a record since replaced: folded, or void
                text = json.dumps({"record": record}) + "\n" + text
            f.write(text.encode("utf-8"))
            f.flush()
            size = os.fstat(f.fileno()).st_size
        if size > max(record[2], FOLD_MIN):
            _write_incomplete(corpus, _current(corpus))


def _record_incomplete(corpus: Path, found: dict[str, dict[str, str | None]],
                       afresh: frozenset[str] | set[str] = frozenset()) -> None:
    """The languages just built: their incomplete units as this build found
    them. A rebuild of the SAME dump keeps a unit's `tried` and `fetched` (it
    failed at Cellar too, or Cellar gave it: not asked first again until a
    newer dump). A language built from a NEWER dump (`afresh`) records each
    unit afresh, its marks cleared: the newer bulk is asked first, then
    Cellar once more for what it still lacks (review 2026-11-21, Q8: the
    marks were kept through every newer dump, against both docstrings)."""
    with _locked(corpus):
        had = _current(corpus)
        for lang, units in found.items():
            old = {} if lang in afresh else (had.get(lang) or {})
            had[lang] = {celex: {**(inf if isinstance(inf, dict) else {"in_force": inf}),
                                 "tried": (old.get(celex) or {}).get("tried"),
                                 **({"fetched": (old.get(celex) or {}).get("fetched")}
                                    if (old.get(celex) or {}).get("fetched") else {})}
                         for celex, inf in sorted(units.items())}
        _write_incomplete(corpus, had)


def incomplete_due(corpus: Path, langs, *, now: float | None = None) -> list[tuple[str, str, str | None]]:
    """(lang, celex, in_force) of the recorded incomplete units of `langs`
    not held now and not tried: what the per-unit pass asks Cellar for first.
    A unit recorded to RE-FORM (held, lacking the Formex package its tree
    lists, which the dump does not give) is due while it still lacks it.

    A UNIT WAITING OUT AN EPISODE IS NOT DUE (review 2026-11-21, Q2): Cellar
    failing on it as an episode (a 5xx, a timeout — `mark_deferred`) is no
    verdict, so it is asked again, but after the engine's wait for its n-th
    failure (engine.retry_wait_s, the one rule of every collection's
    backoff). Until then a single such unit kept this list non-empty pass
    after pass, and the complete listing — every new act — never ran.

    A HELD UNIT'S STATE BY A STAT (review 2026-11-21, Q5: each listing read
    and parsed every held re-form unit's tree and wrapper, 0.9 ms an entry,
    ~3.5 min single-threaded at 232k before the pass asked anything). The
    build records, with each unit to re-form, its tree's identity (`held`,
    `_sig`) and the packages it lacked (`lacks`): while the tree is the same
    file and none of those packages is held, the unit is as the build found
    it — due — and nothing is read. A unit that changed since (landed again,
    a package written in), or recorded before its state was, is judged from
    its files ONCE and its state marked (`whole` where it lacks nothing
    now), so the next listing is a stat again."""
    import stat as _stat
    from ..plugins.eurlex import from_tree, rdf
    from ..plugins.eurlex.source import HISTORY, LANGUAGES, unit_rel
    from .cellar import lacked_packages
    from .engine import retry_wait_s
    import time as _time
    corpus = Path(corpus)
    now = _time.time() if now is None else now
    bases = (str(corpus), os.path.join(str(corpus), HISTORY))
    out = []
    marks: list[tuple[str, str, str, Any]] = []
    for lang, units in sorted(incomplete(corpus).items()):
        if lang not in langs:
            continue
        for celex, entry in sorted(units.items()):
            entry = entry if isinstance(entry, dict) else {}
            if entry.get("tried") or entry.get("fetched"):
                continue                    # Cellar asked once for it: not again until a newer dump
            if any(isinstance(w, dict) and now < float(w.get("at") or 0) + retry_wait_s(int(w.get("n") or 0))
                   for w in (entry.get("deferred"), entry.get("waiting"))):
                continue                    # waiting out its episode or its window: the listing has its turn
            # where it is held (`_held_at`), and its tree's identity, from one stat
            rel = unit_rel(lang, celex)
            at = sig = None
            for base in bases:
                try:
                    st = os.stat(f"{base}/{rel}/{rdf.TREE_FILE}")
                except OSError:
                    continue
                if _stat.S_ISREG(st.st_mode):
                    at, sig = f"{base}/{rel}", [st.st_ino, st.st_mtime_ns, st.st_size]
                    break
            if at is not None:
                if not entry.get("reform"):
                    continue
                held, lacks = entry.get("held"), entry.get("lacks")
                if held is not None and isinstance(lacks, list) and held == sig \
                        and not any(os.path.isfile(f"{at}/{n}") for n in lacks):
                    if entry.get("whole"):
                        continue            # judged whole, unchanged since
                else:
                    unit = Path(at)
                    try:
                        note = from_tree.notice(rdf.loads((unit / rdf.TREE_FILE).read_bytes()), LANGUAGES[lang],
                                                celex)
                    except (OSError, ValueError, from_tree.TreeError):
                        note = None
                    lacked = lacked_packages(unit, note) if note is not None else []
                    whole = note is not None and not lacked and not held_not_its_name(unit, celex)
                    marks.append((lang, celex, "state", {"held": sig, "lacks": lacked, "whole": whole}))
                    if whole:
                        continue
            out.append((lang, celex, entry.get("in_force")))
    _mark(corpus, marks)
    # BY ACT, then language (as the listing, `ORDER BY ?celex ?language`): a
    # work's tree is asked once and its other languages take it from
    # work_trees.RecentTrees (6 h). Language by language, one language's
    # ~9.7k units (~5.7 h at the 1,695 units an hour measured after #759)
    # outlived the cache, and each work's tree (~7 s of a unit's ~7.2 s)
    # was asked again in every language
    out.sort(key=lambda t: (t[1], t[0]))
    return out


def mark_fetched(corpus: Path, lang: str, celex: str) -> None:
    """A recorded unit Cellar gave: not asked first again (a re-form that
    Cellar serves as wrongly as the dump would otherwise be asked every
    pass); a newer dump's build records it afresh. One line appended (Q5)."""
    import time as _time
    _mark(corpus, [(lang, celex, "fetched", _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()))])


def mark_tried(corpus: Path, lang: str, celex: str, why: str) -> None:
    """A recorded incomplete unit Cellar did not give either (a failure that
    is not the endpoint going away): not asked first again."""
    _mark(corpus, [(lang, celex, "tried", why[:300])])


def mark_waiting(corpus: Path, lang: str, celex: str, why: str) -> None:
    """A recorded unit asked first that WAITS for its listing window
    (cellar.WindowNotListed): not due again before `retry_wait_s(n)` of its
    own count — apart from Cellar's episodes (`mark_deferred`) — or until
    the window comes back listed (`mark_listed`)."""
    import time as _time
    _mark(corpus, [(lang, celex, "waiting", {"at": _time.time(), "why": why[:300]})])


def mark_listed(corpus: Path, units: list[tuple[str, str]]) -> None:
    """Recorded units whose listing window was LISTED this pass (cellar
    TreeUnits.prepare): the wait for it is over, its count from 0; a
    Cellar episode's deferral keeps its own (architecture's reviews of
    7eb82ba1, 4, and d863230c, Y1/Y2). One write. A unit still inside its
    wait is not among the pass's pending, so it is reached only when its
    wait ends: a delay of up to 2 h, not "at once"."""
    import time as _time
    when = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    _mark(corpus, [(lang, celex, "listed", when) for lang, celex in units])


def mark_deferred(corpus: Path, lang: str, celex: str, why: str) -> None:
    """A recorded unit asked first that Cellar failed as an EPISODE (the
    endpoint, not the unit): not due again before the engine's wait for its
    n-th such failure (`incomplete_due`, Q2)."""
    import time as _time
    _mark(corpus, [(lang, celex, "deferred", {"at": _time.time(), "why": why[:300]})])


def held_not_its_name(unit: Path, celex: str) -> list[str]:
    """Why a held Formex unit's files are not the ones their names say
    (formex.not_its_name, from each file's first bytes); [] where they are,
    or where the unit holds no Formex wrapper of this act (a package, a
    rendition: nothing to judge here)."""
    from ..plugins.eurlex import formex
    from ..plugins.eurlex.source import unit_files
    try:
        entries = unit_files(unit)
    except OSError:
        return []
    paths = {e.name: e.path for e in entries}
    if not any(formex.is_wrapper(n) for n in paths):
        return []
    try:
        wname = formex.own_wrapper(paths, celex, lambda n: Path(paths[n]).read_bytes())
        wrapper = formex._parse(Path(paths[wname]).read_bytes(), wname)
        heads = {}
        for n, p in paths.items():
            with open(p, "rb") as f:
                heads[n] = f.read(formex._HEAD_BYTES)
    except (OSError, formex.FormexError):
        return []
    return formex.not_its_name(heads, wrapper, wname)


def _held_at(corpus: Path, rel: str) -> Path | None:
    from ..plugins.eurlex import rdf
    from ..plugins.eurlex.source import HISTORY
    for base in (corpus, corpus / HISTORY):
        if (base / rel / rdf.TREE_FILE).is_file():
            return base / rel
    return None


def _held_modified(unit: Path, code: str, celex: str):
    from ..plugins.eurlex import from_tree, rdf
    from .cellar import _epoch
    note = from_tree.notice(rdf.loads((unit / rdf.TREE_FILE).read_bytes()), code, celex)
    return _epoch(note.modified or note.last_modified)


def _older(unit: Path, note, code: str, celex: str) -> bool:
    """Whether the held unit's work was last modified before the dump's."""
    from ..plugins.eurlex import from_tree
    from .cellar import _epoch
    try:
        a = _held_modified(unit, code, celex)
    except (OSError, ValueError, from_tree.TreeError):
        return True                         # unreadable: the dump's replaces it
    b = _epoch(note.modified or note.last_modified)
    return a is not None and b is not None and a < b


def _newer(unit: Path, note, code: str, celex: str) -> bool:
    """Whether the held unit's work was last modified AFTER the dump's: a
    state the dump does not hold (an unreadable tree is not newer)."""
    from ..plugins.eurlex import from_tree
    from .cellar import _epoch
    try:
        a = _held_modified(unit, code, celex)
    except (OSError, ValueError, from_tree.TreeError):
        return False
    b = _epoch(note.modified or note.last_modified)
    return a is not None and b is not None and a > b


def build_chunk(mtd_paths: tuple[str, ...], uuids: list[str], langs: list[tuple[str, str]],
                corpus: str, refresh: bool = False) -> dict[str, Counter]:
    """Every unit the dump gives for these works in these languages
    (`langs`: (code, folder)). A child's task (parallel.each_part).
    `refresh` (a dump newer than the one built in): a held unit whose work
    the dump states modified later is rebuilt from it, where it is held."""
    from ..plugins.eurlex import formex, from_tree, rdf
    from ..plugins.eurlex.source import LANGUAGES, unit_rel
    from .cellar import form_of, lacked_packages, lacks_package, land_many, next_form
    root = Path(corpus)
    counts: dict[str, Counter] = {}
    # GROUP COMMIT (cellar.land_many): units land in batches of LAND_BATCH or
    # LAND_BYTES, two filesystem syncs a batch instead of one fsync a file
    pending: list[tuple[Path, dict[str, bytes]]] = []
    pending_rel: set[str] = set()
    pending_bytes = 0

    def flush() -> None:
        nonlocal pending_bytes
        land_many(root, pending)
        pending.clear()
        pending_rel.clear()
        pending_bytes = 0
    trees: dict[str, tuple[dict, str] | str] = {}
    #: works whose tree names a zip anywhere: the only ones whose held units
    #: can lack a Formex package (formex.unpack), checked on disk
    zipped: set[str] = set()
    incomplete_found: dict[str, dict[str, Any]] = {}

    def package_lacked(at: Path | None, code: str, celex: str):
        """The held unit's OWN tree's note where that unit lacks the Formex
        package its tree lists (landed while `form_of` read a packaged
        manifestation as XHTML, 2026-11-19), else None. Its own tree, never
        the dump's: a unit the API landed may be newer than the dump."""
        if at is None:
            return None
        try:
            note = from_tree.notice(rdf.loads((at / rdf.TREE_FILE).read_bytes()), code, celex)
        except (OSError, ValueError, from_tree.TreeError):
            return None
        return note if lacks_package(at, note) else None

    def record(lang: str, celex: str, note, reform: bool, at: Path | None = None, held_note=None,
               why: str | None = None) -> None:
        """A unit left to the API; one to re-form with its state as found
        (`held`: its tree's identity, `lacks`: the packages it lacks), which
        incomplete_due reads by a stat (Q5); `why` where the dump holds it
        and does not give it (a member that does not read)."""
        inf = None if note.in_force is None else ("true" if note.in_force else "false")
        if not reform:
            incomplete_found.setdefault(lang, {})[celex] = {"in_force": inf, "why": why} if why else inf
            return
        entry: dict[str, Any] = {"in_force": inf, "reform": True, **({"why": why} if why else {})}
        if at is not None:
            entry["held"] = _sig(at / rdf.TREE_FILE)
            entry["lacks"] = lacked_packages(at, held_note) if held_note is not None else []
        incomplete_found.setdefault(lang, {})[celex] = entry
    zips = _mtd(mtd_paths)
    for uuid in uuids:
        data = None
        cut = False
        for z in zips:
            try:
                data = z.read(f"{uuid}/tree_non_inferred.rdf")
                break
            except KeyError:
                continue
            except (zipfile.BadZipFile, OSError, EOFError):
                # one work's tree that does not read, in an MTD that opens: as a
                # tree that does not parse (below), counted; its CELEX is in it,
                # so its units are the listing's, never held back for it
                cut = True
                break
        if cut:
            trees[uuid] = "mtd-member-unreadable"
            continue
        if data is None:
            trees[uuid] = "mtd-missing"
            continue
        try:
            tree = rdf.read(data)
        except rdf.RdfError:
            trees[uuid] = "mtd-unreadable"
            continue
        celex = _work_celex(tree, uuid)
        trees[uuid] = (tree, celex) if celex else "no-celex"
        if b".zip" in data:
            zipped.add(uuid)
    for lang, folder in langs:
        c = counts.setdefault(lang, Counter())
        lz = _lang(folder)
        code = LANGUAGES[lang]
        for uuid in uuids:
            got = trees[uuid]
            held_forms = lz.index.get(uuid)
            if not held_forms:
                # a unit the API landed for a language the dump lacks, holding
                # XHTML for a packaged Formex: the API's to re-form, recorded
                at = _held_at(root, unit_rel(lang, got[1])) if uuid in zipped and not isinstance(got, str) \
                    else None
                lacked = package_lacked(at, code, got[1]) if at is not None else None
                if lacked is not None:
                    c["incomplete"] += 1
                    c["package-not-in-dump"] += 1
                    record(lang, got[1], lacked, True, at, lacked)
                else:
                    c["not-in-language"] += 1
                continue
            if isinstance(got, str):
                c[got] += 1
                continue
            tree, celex = got
            rel = unit_rel(lang, celex)
            if str(rel) in pending_rel:
                c["held"] += 1                  # this chunk already lands it
                continue
            at = _held_at(root, rel)
            # A HELD UNIT LACKING ITS FORMEX PACKAGE (2026-11-19): `form_of`
            # read a manifestation served as one zip as XHTML; it is re-formed
            # from the dump, or recorded for the API where the dump lacks it
            held_note = package_lacked(at, code, celex) if uuid in zipped else None
            reform = held_note is not None
            if at is not None and not reform and held_not_its_name(at, celex):
                # A HELD UNIT WHOSE FILES ARE NOT THEIR NAMES' (2026-11-19: the
                # dump's 9 acts of 2014–15): the dump gives the same bytes, so
                # the API's — Cellar serves them right (32015R0207 de)
                try:
                    note = from_tree.notice(rdf.scope(tree, code), code, celex)
                except from_tree.TreeError:
                    c["no-work"] += 1
                    continue
                c["incomplete"] += 1
                c["dump-not-its-name"] += 1
                record(lang, celex, note, True, at)
                continue
            if at is not None and not refresh and not reform:
                c["held"] += 1
                continue
            scoped = rdf.scope(tree, code)
            try:
                note = from_tree.notice(scoped, code, celex)
            except from_tree.TreeError:
                c["no-work"] += 1
                continue
            if at is not None and not _older(at, note, code, celex):
                if not reform:
                    c["held"] += 1
                    continue
                if _newer(at, note, code, celex):
                    # held NEWER than the dump: what changed since it was cut is the API's
                    c["incomplete"] += 1
                    c["package-not-in-dump"] += 1
                    record(lang, celex, note, True, at, held_note)
                    continue
                # the dump's own state: re-formed from it
            form = form_of(note)
            listed = note.items.get(form) or [] if form else []
            if not listed or any(not it.name or it.name not in held_forms.get(form, {}) for it in listed):
                c["incomplete"] += 1           # the API's (phase 2), never another form
                if reform:
                    c["package-not-in-dump"] += 1
                record(lang, celex, note, reform, at, held_note)
                continue
            try:
                files = {it.name: lz.read(held_forms[form][it.name]) for it in listed}
            except MemberUnreadable as e:
                # the dump holds it and cannot give it: the API's, with the
                # reason; a held unit (refreshed, re-formed) stays as held
                c["incomplete"] += 1
                c["member-unreadable"] += 1
                record(lang, celex, note, at is not None, at, held_note, str(e))
                continue
            if form == "fmx4":
                try:
                    view = formex.unpack(files)[0]      # a package read through
                    text_wrapper = formex.own_wrapper(view, celex, view.__getitem__)   # cellar's rule
                except formex.FormexError:
                    view, text_wrapper = files, None
                if text_wrapper is None:
                    # no wrapper of THIS act: the next form the tree lists is
                    # its text, landed beside the Formex items (cellar.next_form)
                    alt = next_form(note)
                    alt_listed = note.items.get(alt) or [] if alt else []
                    if not alt_listed or any(it.name in files for it in alt_listed):
                        c["formex-refused"] += 1
                        continue
                    if any(it.name not in held_forms.get(alt, {}) for it in alt_listed):
                        c["incomplete"] += 1       # that form the API's (phase 2)
                        record(lang, celex, note, reform, at, held_note)
                        continue
                    try:
                        files.update({it.name: lz.read(held_forms[alt][it.name]) for it in alt_listed})
                    except MemberUnreadable as e:
                        c["incomplete"] += 1
                        c["member-unreadable"] += 1
                        record(lang, celex, note, at is not None, at, held_note, str(e))
                        continue
                    c["formex-not-this-act"] += 1
                # a text in another language under this one's expression is
                # held as the origin files it (operator, 2026-11-16, B): the
                # parser labels it; counted here so the pass says how many
                if text_wrapper is not None:
                    wrong = formex.not_its_name(view, formex._parse(view[text_wrapper], text_wrapper),
                                                text_wrapper)
                    if wrong:
                        # never landed from the dump: the API's (Cellar serves it right)
                        c["incomplete"] += 1
                        c["dump-not-its-name"] += 1
                        record(lang, celex, note, at is not None, at, held_note)
                        continue
                stated = _LG_DOC.search(view[text_wrapper]) if text_wrapper is not None else None
                if stated and stated.group(1).decode().lower() != lang:
                    c["text-in-another-language"] += 1
            files[rdf.TREE_FILE] = rdf.dumps(scoped)
            size = sum(len(b) for b in files.values())
            pending.append((at if at is not None else root / rel, files))
            pending_rel.add(str(rel))
            pending_bytes += size
            c["reformed" if reform else "refreshed" if at is not None else "built"] += 1
            c["bytes"] += size
            if len(pending) >= LAND_BATCH or pending_bytes >= LAND_BYTES:
                flush()
    flush()
    counts[_INCOMPLETE_KEY] = incomplete_found      # type: ignore[assignment] — popped by `build`
    return counts


#: build_chunk's units left incomplete, beside its counts (popped by `build`)
_INCOMPLETE_KEY = "_incomplete"
#: A BUILD STOPPED AT A CHUNK BOUNDARY RESUMES AFTER ITS CHUNKS (review
#: 2026-11-21, Q7): one JSON line a chunk whose units landed — its index, its
#: counts, the units it left to the API — under the run it belongs to (the
#: dump's languages and creation, refresh, the chunk size, the works). Read by
#: the next build of the same run, which computes only the chunks not in it;
#: removed once the build completes and its stamp is written. A line lost to
#: a crash costs that chunk again (its units are found held). Appended with
#: one write(2) on O_APPEND: two builds at once do not interleave a line
PROGRESS = ".eurlex-dump-progress.jsonl"


class Stopped(TransferAborted):
    """The build stopped at a chunk boundary on a stop (SIGTERM, a pause):
    every unit of the chunks done landed and recorded (PROGRESS), the stamp
    not written; the next build resumes after them (Q7, invariant 2)."""


def _stop_set(stop: Callable[[], bool] | None) -> bool:
    return stop is not None and bool(stop())


def _run_id(dumps: dict[str, str], refresh: bool, chunk: int, uuids: list[str]) -> str:
    import hashlib
    import json
    h = hashlib.sha256(json.dumps({"dump": dumps, "refresh": refresh, "chunk": chunk},
                                  sort_keys=True).encode("utf-8"))
    for u in uuids:
        h.update(u.encode("utf-8") + b"\n")
    return h.hexdigest()[:24]


def _progress(corpus: Path, run: str) -> dict[int, dict]:
    """The chunks a build of `run` landed: `{index: its line}`."""
    import json
    try:
        lines = (Path(corpus) / PROGRESS).read_bytes().splitlines()
    except OSError:
        return {}
    out: dict[int, dict] = {}
    for line in lines:
        try:
            got = json.loads(line)
        except ValueError:
            continue                        # a line a crash cut
        if isinstance(got, dict) and got.get("run") == run and isinstance(got.get("k"), int):
            out[got["k"]] = got
    return out


def _progress_add(corpus: Path, run: str, k: int, counts: dict, found: dict) -> None:
    import json
    line = json.dumps({"run": run, "k": k, "counts": {l: dict(c) for l, c in counts.items()},
                       "incomplete": found}, sort_keys=True, separators=(",", ":")) + "\n"
    fd = os.open(Path(corpus) / PROGRESS, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


#: ONE BUILD OF A CORPUS AT A TIME (coordinator, 2026-11-21): the CLI build
#: (`louise dev eurlex-dump`) and the worker's prepare (build_newer) could
#: both land the same units, each clearing the other's staging. An exclusive,
#: NON-BLOCKING flock(2) on this file for the whole build — released by the
#: kernel when its holder dies — holding the holder's pid; the second build
#: never waits (BuildRunning)
BUILD_LOCK = ".eurlex-dump-build.lock"


class BuildRunning(HeldBack):
    """Another build of this corpus holds BUILD_LOCK: this one does not wait.
    The worker's pass is held back, said (spec.HeldBack); the CLI exits 3,
    deferred (deploy/supervise-pass.sh asks again later)."""


@contextmanager
def _build_lock(corpus: Path):
    import fcntl
    import time as _time
    path = Path(corpus) / BUILD_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = os.pread(fd, 200, 0).decode("utf-8", errors="replace").strip()
            raise BuildRunning(f"a dump build is running on {corpus} ({holder or 'its pid not written yet'}): "
                               f"this one does not wait; asked again later") from None
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"pid {os.getpid()} since {_time.strftime('%Y-%m-%dT%H:%M:%SZ', _time.gmtime())}\n"
                  .encode("utf-8"), 0)
        yield
    finally:
        os.close(fd)                        # the lock with it


def build(dump_dir: Path, corpus: Path, languages: list[str] | None = None, *,
          log: Callable[[str], None] = print, chunk: int = CHUNK, refresh: bool = False,
          stop: Callable[[], bool] | None = None) -> dict[str, Counter]:
    """Every unit of `languages` (None: every language the dump holds) the
    dump gives, into `corpus`; then the stamp names each language's dump.
    Returns the counts by language. `stop()` true: the build ends at the next
    chunk boundary, raising Stopped — what it built landed, its chunks
    recorded (PROGRESS), the stamp unwritten — and the next build of the same
    dump resumes after those chunks (review 2026-11-21, Q7: no stop was read
    during a build, and a SIGTERM waited for a ~932k-unit refresh). The
    chunks in flight in the pool's children finish first: one a child.
    Another build of the corpus running: BuildRunning at once (BUILD_LOCK)."""
    with _build_lock(corpus):
        return _build(dump_dir, corpus, languages, log=log, chunk=chunk, refresh=refresh, stop=stop)


def _build(dump_dir: Path, corpus: Path, languages: list[str] | None, *, log: Callable[[str], None],
           chunk: int, refresh: bool, stop: Callable[[], bool] | None) -> dict[str, Counter]:
    import contextlib
    from .. import parallel
    from .datadump import Dumps
    found = Dumps(dump_dir, log=log)._scan()
    langs = sorted(found) if languages is None else [l for l in languages if l in found]
    missing = sorted(set(languages or ()) - set(found))
    if missing:
        log(f"[eurlex-dump] no dump of {', '.join(missing)} under {dump_dir} (a folder with zips "
            f"and its datadump.json): those languages are not built")
    mtd = mtd_zips([found[l][0] for l in langs] or [dump_dir])
    if not mtd:
        raise FileNotFoundError(f"{dump_dir}: no MTD zip (LEG_MTD.zip): the trees are the units' "
                                f"facts, nothing is built without them")
    uuids = works(mtd)
    log(f"[eurlex-dump] {len(uuids)} works in {', '.join(p.name for p in mtd)}; languages "
        f"{', '.join(langs)}; into {corpus}")
    pairs = [(l, str(found[l][0])) for l in langs]
    tasks = [(tuple(str(p) for p in mtd), uuids[i:i + chunk], pairs, str(corpus), refresh)
             for i in range(0, len(uuids), chunk)]
    total: dict[str, Counter] = {l: Counter() for l in langs}
    found_incomplete: dict[str, dict[str, str | None]] = {l: {} for l in langs}

    def take(counts: dict, units: dict) -> None:
        for l, got_units in (units or {}).items():
            found_incomplete.setdefault(l, {}).update(got_units)
        for l, c in counts.items():
            total.setdefault(l, Counter()).update(c)
    run = _run_id({l: found[l][1].isoformat() for l in langs}, refresh, chunk, uuids)
    done = _progress(corpus, run)
    if done:
        for k in sorted(done):
            take(done[k].get("counts") or {}, done[k].get("incomplete") or {})
        log(f"[eurlex-dump] resumed: {len(done)}/{len(tasks)} chunks landed before, not built again")
    else:
        (Path(corpus) / PROGRESS).unlink(missing_ok=True)       # another run's, or none
        Path(corpus).mkdir(parents=True, exist_ok=True)
    todo = [k for k in range(len(tasks)) if k not in done]
    if todo and _stop_set(stop):
        raise Stopped(f"stopped before the build: {len(done)}/{len(tasks)} chunks landed, the stamp not written")
    with contextlib.closing(parallel.each_part(build_chunk, [tasks[k] for k in todo])) as parts:
        for n, (k, got) in enumerate(zip(todo, parts), len(done) + 1):
            units = got.pop(_INCOMPLETE_KEY, None) or {}
            take(got, units)
            _progress_add(corpus, run, k, got, units)          # after the chunk's units landed
            if n % 10 == 0 or n == len(tasks):
                built = sum(c["built"] for c in total.values())
                log(f"[eurlex-dump] {n}/{len(tasks)} chunks: {built} units built")
            if n < len(tasks) and _stop_set(stop):
                # the chunks in flight finish as the pool closes (one a child)
                raise Stopped(f"stopped at a chunk boundary: {n}/{len(tasks)} chunks landed and recorded (those "
                              f"in flight land as the pool closes), the stamp not written; the next build "
                              f"resumes after them")
    for l in langs:
        c = total[l]
        log(f"[eurlex-dump] {l}: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items()) if k != "bytes")
            + f"; {c['bytes'] >> 20} MiB")
    # every work visited for every language: the dump is used whole for them,
    # and what it lacks is named for the per-unit pass to ask first — afresh
    # for a language whose dump is newer than the one built in (Q8), or none
    # was stamped (its record's marks are of no dump known)
    from .datadump import _stamp as when
    had = stamp(corpus)
    afresh = {l for l in langs if l not in had or (when(had[l]) or found[l][1]) < found[l][1]}
    _record_incomplete(corpus, found_incomplete, afresh)
    _stamp(corpus, {l: found[l][1].isoformat() for l in langs})
    _mark_packages(corpus, langs)
    (Path(corpus) / PROGRESS).unlink(missing_ok=True)          # complete: nothing to resume
    return total


def build_newer(dump_dir: Path, corpus: Path, languages: list[str], *,
                log: Callable[[str], None] = print, stop: Callable[[], bool] | None = None) -> dict[str, Counter]:
    """Before a per-unit pass (cellar.TreeUnits.prepare): the languages whose
    dump under `dump_dir` is newer than the one built into `corpus` are built
    from it first, a held unit the newer dump states modified later
    refreshed in place — the bulk before the API, whenever a newer bulk is
    there. Nothing for a language whose dump was built in already. Reading the
    folders' statements asks nothing of the origin."""
    from .datadump import Dumps, _stamp as when
    found = Dumps(dump_dir, log=log)._scan()
    had = stamp(corpus)
    newer = [l for l in languages if l in found
             and (l not in had or (when(had[l]) or found[l][1]) < found[l][1])]
    if not newer:
        return {}
    log(f"[eurlex-dump] a newer Data Dump of {', '.join(newer)}: built before Cellar is asked")
    return build(dump_dir, corpus, newer, log=log, refresh=True, stop=stop)


# ---- the units an earlier per-unit fetch landed (RULE 1) --------------------------------------

def carry_chunk(units: list[str], corpus: str) -> Counter:
    """Each held parser-3 unit (`<old>/lang=<l>/<yyyy>/<CELEX>`) the new
    corpus does not hold: its tree and its form's items linked in — the same
    bytes — without its notice, at its place (module docstring)."""
    from ..plugins.eurlex import from_tree, rdf
    from ..plugins.eurlex.source import HISTORY, LANGUAGES, _CONSOLIDATED, unit_rel
    from .cellar import form_of
    root = Path(corpus)
    c: Counter = Counter()
    for u in units:
        unit = Path(u)
        celex, lang = unit.name, unit.parent.parent.name.partition("=")[2]
        if lang not in LANGUAGES or not (unit / rdf.TREE_FILE).is_file():
            c["not-a-unit"] += 1
            continue
        rel = unit_rel(lang, celex)
        if _held(root, rel):
            c["held"] += 1
            continue
        try:
            scoped = rdf.loads((unit / rdf.TREE_FILE).read_bytes())
            note = from_tree.notice(scoped, LANGUAGES[lang], celex)
        except (OSError, ValueError, from_tree.TreeError):
            c["tree-unreadable"] += 1
            continue
        form = form_of(note)
        names = sorted(it.name or "" for it in (note.items.get(form) or [] if form else []))
        if not names or any(not n or not (unit / n).is_file() for n in names):
            c["form-differs"] += 1             # the tree's form is not what it holds: the API's
            continue
        history = bool(_CONSOLIDATED.match(celex)) or note.in_force is not True
        dest = root / (HISTORY if history else "") / rel
        _link_unit(unit, dest, names + [rdf.TREE_FILE])
        c["history" if history else "base"] += 1
    return c


def _link_unit(unit: Path, dest: Path, names: list[str]) -> None:
    """The files hard-linked (copied across devices) into a staging folder
    beside `dest`, then swapped in; the tree last, as a landing writes it."""
    import shutil
    from ..plugins.eurlex import rdf
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f".{dest.name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    for n in sorted(names, key=lambda n: (n == rdf.TREE_FILE, n)):
        try:
            os.link(unit / n, staging / n)
        except OSError:
            shutil.copy2(unit / n, staging / n)
    os.replace(staging, dest)


def carry(old: Path, corpus: Path, *, log: Callable[[str], None] = print, chunk: int = 2000) -> Counter:
    """`carry_chunk` over every unit of `old` (a parser-3 corpus), in chunks
    over the parse pool."""
    from .. import parallel
    units = sorted(str(p) for lang in sorted(old.glob("lang=*")) for p in lang.glob("*/*") if p.is_dir()
                   and not p.name.startswith("."))
    total: Counter = Counter()
    for got in parallel.each_part(carry_chunk, [(units[i:i + chunk], str(corpus))
                                                for i in range(0, len(units), chunk)]):
        total.update(got)
    log(f"[eurlex-dump] carried from {old}: " + ", ".join(f"{k} {v}" for k, v in sorted(total.items())))
    return total
