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
from pathlib import Path
from typing import Any, Callable

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


class LangZips:
    """One language's item zips, indexed `uuid → form → name → (zip, info)`."""

    def __init__(self, folder: Path, log=print) -> None:
        from .datadump import _staged
        self.zips: list[zipfile.ZipFile] = []
        self.index: dict[str, dict[str, dict[str, tuple[int, zipfile.ZipInfo]]]] = {}
        for p in sorted(folder.rglob("*.zip")):
            if "MTD" in p.name.upper():
                continue
            try:
                # the PDF zips (~22 GB a language, 530 GB in all) are read in
                # place: their members are asked for only by the few acts with
                # no Formex and no HTML, and staging one would fill the cache
                zf = zipfile.ZipFile(p if "PDF" in p.name.upper() else _staged(p, log))
            except (zipfile.BadZipFile, OSError) as e:
                log(f"[eurlex-dump] {p}: not read ({e}); its items are left to the API")
                continue
            i = len(self.zips)
            self.zips.append(zf)
            for info in zf.infolist():
                m = _MEMBER.match(info.filename)
                if m:
                    self.index.setdefault(m["uuid"], {}).setdefault(m["form"], {})[m["name"]] = (i, info)

    def read(self, ref: tuple[int, zipfile.ZipInfo]) -> bytes:
        i, info = ref
        return self.zips[i].read(info)          # its CRC-32 checked

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
        got = _MTDS[paths] = [zipfile.ZipFile(_staged(Path(p), print)) for p in paths]
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
    out: set[str] = set()
    for p in mtd:
        with zipfile.ZipFile(p) as z:
            out.update(m["uuid"] for m in map(_MTD_MEMBER.match, z.namelist()) if m)
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
_incomplete_lock = threading.Lock()


def incomplete(corpus: Path) -> dict[str, dict[str, dict]]:
    import json
    try:
        got = json.loads((Path(corpus) / INCOMPLETE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _write_incomplete(corpus: Path, data: dict) -> None:
    import json
    path = Path(corpus) / INCOMPLETE
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _record_incomplete(corpus: Path, found: dict[str, dict[str, str | None]]) -> None:
    """The languages just built: their incomplete units as this build found
    them, a unit tried before keeping its `tried` (it failed at Cellar too,
    and is not asked again until a newer dump)."""
    with _incomplete_lock:
        had = incomplete(corpus)
        for lang, units in found.items():
            old = had.get(lang) or {}
            had[lang] = {celex: {**(inf if isinstance(inf, dict) else {"in_force": inf}),
                                 "tried": (old.get(celex) or {}).get("tried")}
                         for celex, inf in sorted(units.items())}
        _write_incomplete(corpus, had)


def incomplete_due(corpus: Path, langs) -> list[tuple[str, str, str | None]]:
    """(lang, celex, in_force) of the recorded incomplete units of `langs`
    not held now and not tried: what the per-unit pass asks Cellar for first.
    A unit recorded to RE-FORM (held, lacking the Formex package its tree
    lists, which the dump does not give) is due while it still lacks it."""
    from ..plugins.eurlex import from_tree, rdf
    from ..plugins.eurlex.source import LANGUAGES, unit_rel
    from .cellar import lacks_package
    out = []
    for lang, units in sorted(incomplete(corpus).items()):
        if lang not in langs:
            continue
        for celex, entry in sorted(units.items()):
            entry = entry or {}
            if entry.get("tried"):
                continue
            at = _held_at(Path(corpus), unit_rel(lang, celex))
            if at is not None:
                if not entry.get("reform"):
                    continue
                try:
                    note = from_tree.notice(rdf.loads((at / rdf.TREE_FILE).read_bytes()), LANGUAGES[lang], celex)
                except (OSError, ValueError, from_tree.TreeError):
                    note = None
                if note is not None and not lacks_package(at, note):
                    continue
            out.append((lang, celex, entry.get("in_force")))
    return out


def mark_tried(corpus: Path, lang: str, celex: str, why: str) -> None:
    """A recorded incomplete unit Cellar did not give either (a failure that
    is not the endpoint going away): not asked first again."""
    with _incomplete_lock:
        had = incomplete(corpus)
        entry = (had.get(lang) or {}).get(celex)
        if entry is None:
            return
        entry["tried"] = why[:300]
        _write_incomplete(corpus, had)


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
    return _epoch(from_tree.notice(rdf.loads((unit / rdf.TREE_FILE).read_bytes()), code, celex).last_modified)


def _older(unit: Path, note, code: str, celex: str) -> bool:
    """Whether the held unit's work was last modified before the dump's."""
    from ..plugins.eurlex import from_tree
    from .cellar import _epoch
    try:
        a = _held_modified(unit, code, celex)
    except (OSError, ValueError, from_tree.TreeError):
        return True                         # unreadable: the dump's replaces it
    b = _epoch(note.last_modified)
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
    b = _epoch(note.last_modified)
    return a is not None and b is not None and a > b


def build_chunk(mtd_paths: tuple[str, ...], uuids: list[str], langs: list[tuple[str, str]],
                corpus: str, refresh: bool = False) -> dict[str, Counter]:
    """Every unit the dump gives for these works in these languages
    (`langs`: (code, folder)). A child's task (parallel.each_part).
    `refresh` (a dump newer than the one built in): a held unit whose work
    the dump states modified later is rebuilt from it, where it is held."""
    from ..plugins.eurlex import formex, from_tree, rdf
    from ..plugins.eurlex.source import LANGUAGES, unit_rel
    from .cellar import form_of, lacks_package, land_many, next_form
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

    def record(lang: str, celex: str, note, reform: bool) -> None:
        inf = None if note.in_force is None else ("true" if note.in_force else "false")
        incomplete_found.setdefault(lang, {})[celex] = {"in_force": inf, "reform": True} if reform else inf
    zips = _mtd(mtd_paths)
    for uuid in uuids:
        data = None
        for z in zips:
            try:
                data = z.read(f"{uuid}/tree_non_inferred.rdf")
                break
            except KeyError:
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
                lacked = package_lacked(_held_at(root, unit_rel(lang, got[1])), code, got[1]) \
                    if uuid in zipped and not isinstance(got, str) else None
                if lacked is not None:
                    c["incomplete"] += 1
                    c["package-not-in-dump"] += 1
                    record(lang, got[1], lacked, True)
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
            reform = uuid in zipped and package_lacked(at, code, celex) is not None
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
                    record(lang, celex, note, True)
                    continue
                # the dump's own state: re-formed from it
            form = form_of(note)
            listed = note.items.get(form) or [] if form else []
            if not listed or any(not it.name or it.name not in held_forms.get(form, {}) for it in listed):
                c["incomplete"] += 1           # the API's (phase 2), never another form
                if reform:
                    c["package-not-in-dump"] += 1
                record(lang, celex, note, reform)
                continue
            try:
                files = {it.name: lz.read(held_forms[form][it.name]) for it in listed}
            except (zipfile.BadZipFile, OSError):
                c["unreadable"] += 1
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
                        record(lang, celex, note, reform)
                        continue
                    try:
                        files.update({it.name: lz.read(held_forms[alt][it.name]) for it in alt_listed})
                    except (zipfile.BadZipFile, OSError):
                        c["unreadable"] += 1
                        continue
                    c["formex-not-this-act"] += 1
                # a text in another language under this one's expression is
                # held as the origin files it (operator, 2026-11-16, B): the
                # parser labels it; counted here so the pass says how many
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


def build(dump_dir: Path, corpus: Path, languages: list[str] | None = None, *,
          log: Callable[[str], None] = print, chunk: int = CHUNK, refresh: bool = False) -> dict[str, Counter]:
    """Every unit of `languages` (None: every language the dump holds) the
    dump gives, into `corpus`; then the stamp names each language's dump.
    Returns the counts by language."""
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
    for k, got in enumerate(parallel.each_part(build_chunk, tasks), 1):
        for l, units in (got.pop(_INCOMPLETE_KEY, None) or {}).items():
            found_incomplete.setdefault(l, {}).update(units)
        for l, c in got.items():
            total[l].update(c)
        if k % 10 == 0 or k == len(tasks):
            built = sum(c["built"] for c in total.values())
            log(f"[eurlex-dump] {k}/{len(tasks)} chunks: {built} units built")
    for l in langs:
        c = total[l]
        log(f"[eurlex-dump] {l}: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items()) if k != "bytes")
            + f"; {c['bytes'] >> 20} MiB")
    # every work visited for every language: the dump is used whole for them,
    # and what it lacks is named for the per-unit pass to ask first
    _record_incomplete(corpus, found_incomplete)
    _stamp(corpus, {l: found[l][1].isoformat() for l in langs})
    _mark_packages(corpus, langs)
    return total


def build_newer(dump_dir: Path, corpus: Path, languages: list[str], *,
                log: Callable[[str], None] = print) -> dict[str, Counter]:
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
    return build(dump_dir, corpus, newer, log=log, refresh=True)


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
