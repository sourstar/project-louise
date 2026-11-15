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
all (cellar._land: staged, fsynced, swapped).

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
import zipfile
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Callable

#: works per task: a chunk parses its trees once and visits every language
CHUNK = 400
#: the corpus root's record of the dump built into it: `{lang: its stated
#: creation}`, written when a language's build has visited every work. A
#: parser-4 collection asks Cellar nothing per unit before it names every
#: language it holds (cellar.CellarInventory.held_back: bulk first)
STAMP = ".eurlex-dump.json"
#: one language's zips held open per child (their central directories)
OPEN_LANGUAGES = 2
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


def _held_at(corpus: Path, rel: str) -> Path | None:
    from ..plugins.eurlex import rdf
    from ..plugins.eurlex.source import HISTORY
    for base in (corpus, corpus / HISTORY):
        if (base / rel / rdf.TREE_FILE).is_file():
            return base / rel
    return None


def _older(unit: Path, note, code: str, celex: str) -> bool:
    """Whether the held unit's work was last modified before the dump's."""
    from ..plugins.eurlex import from_tree, rdf
    from .cellar import _epoch
    try:
        held = from_tree.notice(rdf.loads((unit / rdf.TREE_FILE).read_bytes()), code, celex).last_modified
    except (OSError, ValueError, from_tree.TreeError):
        return True                         # unreadable: the dump's replaces it
    a, b = _epoch(held), _epoch(note.last_modified)
    return a is not None and b is not None and a < b


def build_chunk(mtd_paths: tuple[str, ...], uuids: list[str], langs: list[tuple[str, str]],
                corpus: str, refresh: bool = False) -> dict[str, Counter]:
    """Every unit the dump gives for these works in these languages
    (`langs`: (code, folder)). A child's task (parallel.each_part).
    `refresh` (a dump newer than the one built in): a held unit whose work
    the dump states modified later is rebuilt from it, where it is held."""
    from ..plugins.eurlex import formex, from_tree, rdf
    from ..plugins.eurlex.source import LANGUAGES, unit_rel
    from .cellar import _land, form_of
    root = Path(corpus)
    counts: dict[str, Counter] = {}
    trees: dict[str, tuple[dict, str] | str] = {}
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
    for lang, folder in langs:
        c = counts.setdefault(lang, Counter())
        lz = _lang(folder)
        code = LANGUAGES[lang]
        for uuid in uuids:
            got = trees[uuid]
            held_forms = lz.index.get(uuid)
            if not held_forms:
                c["not-in-language"] += 1
                continue
            if isinstance(got, str):
                c[got] += 1
                continue
            tree, celex = got
            rel = unit_rel(lang, celex)
            at = _held_at(root, rel)
            if at is not None and not refresh:
                c["held"] += 1
                continue
            scoped = rdf.scope(tree, code)
            try:
                note = from_tree.notice(scoped, code, celex)
            except from_tree.TreeError:
                c["no-work"] += 1
                continue
            if at is not None and not _older(at, note, code, celex):
                c["held"] += 1
                continue
            form = form_of(note)
            listed = note.items.get(form) or [] if form else []
            if not listed or any(not it.name or it.name not in held_forms.get(form, {}) for it in listed):
                c["incomplete"] += 1           # the API's (phase 2), never another form
                continue
            try:
                files = {it.name: lz.read(held_forms[form][it.name]) for it in listed}
            except (zipfile.BadZipFile, OSError):
                c["unreadable"] += 1
                continue
            if form == "fmx4":
                wrappers = [n for n in files if formex.is_wrapper(n)]
                stated = _LG_DOC.search(files[wrappers[0]]) if len(wrappers) == 1 else None
                if len(wrappers) != 1 or (stated and stated.group(1).decode().lower() != lang):
                    c["formex-refused"] += 1
                    continue
            files[rdf.TREE_FILE] = rdf.dumps(scoped)
            _land(at if at is not None else root / rel, files)
            c["refreshed" if at is not None else "built"] += 1
            c["bytes"] += sum(len(b) for b in files.values())
    return counts


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
    for k, got in enumerate(parallel.each_part(build_chunk, tasks), 1):
        for l, c in got.items():
            total[l].update(c)
        if k % 10 == 0 or k == len(tasks):
            built = sum(c["built"] for c in total.values())
            log(f"[eurlex-dump] {k}/{len(tasks)} chunks: {built} units built")
    for l in langs:
        c = total[l]
        log(f"[eurlex-dump] {l}: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items()) if k != "bytes")
            + f"; {c['bytes'] >> 20} MiB")
    # every work visited for every language: the dump is used whole for them
    _stamp(corpus, {l: found[l][1].isoformat() for l in langs})
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
