"""The EUR-Lex DATA DUMP as a local TRANSPORT for Cellar's items (operator,
2026-11-14: "every time there is a bulk download that saves a relevant amount
of single requests to the source, we start from the bulk download first").

WHAT IT SAVES. A Cellar unit costs one request for its notice, one per item
and one for its tree (cellar.fetch_unit). The dump holds every act IN FORCE
in each language, its items under `<work uuid>/<manifestation type>/<item
name>` in the language's zips (München's layout, 2026-11-14: `fmx4/`,
`xhtml/`, `html/`). An item it holds is read from the zip instead of asked
for: at least half the requests of a Formex unit (its wrapper and its body)
and almost all its bytes. The notice and the tree still come from Cellar,
and so does everything the dump does not hold (consolidations, repealed
acts, PDFs) and everything modified since it was made.

THE ORIGIN IS STILL CELLAR. The dump is outside `origin.spec` and the Codex,
as a mirror is (06 §6.2): `records-vs-origin` re-fetches from Cellar
(`CellarUnits.refetch` never passes a dump), and a unit read through the dump
is byte for byte the unit Cellar serves, or the item is not taken.

WHEN AN ITEM IS TAKEN (Cellar publishes no checksum, and its ETag is a
modification time, München 2026-11-14), all of:
  - the notice lists it under that NAME with a size, and the dump's member
    at `<work uuid>/<form>/<name>` has that size — keyed by WORK, never by
    name alone: every act of one OJ issue may ship a `….toc.xml` of the
    same name (31997R1466 and R1467);
  - its MANIFESTATION's `cmr:lastModificationDate` in the tree Cellar serves
    now is before the dump's stated creation less MARGIN (an item node has
    no date of its own; the dump takes hours to make). No date, no take;
  - the zip's CRC-32 holds (zipfile checks it on read).
Anything else is fetched from Cellar as before. WHY THE DATE IS ENOUGH
(operator, 2026-11-14: "Assume that cellar always updates the date when a file
is changed"): an item Cellar changed after the dump carries a later date and is
fetched, so a correction made at Cellar always wins; no byte comparison gates
first use. What would still show a change Cellar did not date:
`records-vs-origin`, whose sample re-fetches from Cellar.

CONFIGURATION (node-local, like the cache dirs, invariant 10's tier 1):
`EURLEX_DATADUMP_DIR` names a directory holding one folder per language, each
with its zips and a `datadump.json` stating `{"language": "it", "created":
"2026-11-08T01:00:00+01:00"}` — the creation the dump's page states. A folder
without it, or with a language it does not hold, is not used, and the log
says why. Unset: no dump, every item from Cellar.

THE STANDARD, NOT ADOPTED: none exists for "a bulk as a transport of a
per-unit origin" beyond the mirror (06 §6.2), whose layout must be the
origin's; this dump's is not, so the mapping is this module's.
"""
from __future__ import annotations

import json
import os
import threading
import zipfile
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENV = "EURLEX_DATADUMP_DIR"
STATEMENT = "datadump.json"
#: the dump's stated creation less this is the last modification it is
#: trusted to hold: the dump is made over hours, and a manifestation changed
#: while it was made may be in it either way
MARGIN = timedelta(days=2)
#: languages whose zips are held open at once (each holds its zips' central
#: directories, ~100 MB for a language of ~200 k items)
OPEN_LANGUAGES = 3
CMR_MODIFIED = "http://publications.europa.eu/ontology/cdm/cmr#lastModificationDate"
_CELLAR = "/resource/cellar/"


def _stamp(text: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None      # an instant without its zone is no instant


def work_uuid(item_uri: str) -> str | None:
    """`…/resource/cellar/<uuid>.<expr>.<manif>/DOC_<n>` → `<uuid>`."""
    head, sep, rest = str(item_uri).partition(_CELLAR)
    if not sep:
        return None
    uuid = rest.split("/", 1)[0].split(".", 1)[0]
    return uuid or None


def manifestation_of(item_uri: str) -> str:
    return str(item_uri).rsplit("/", 1)[0]


def modified(tree: dict, uri: str) -> datetime | None:
    """The node's `cmr:lastModificationDate` in a tree `rdf.read` gave, the
    latest where it states several; None where it states none."""
    values = (tree.get("nodes", {}).get(uri) or {}).get(CMR_MODIFIED) or []
    stamps = [s for s in (_stamp(v.get("@value", "")) for v in values) if s is not None]
    return max(stamps) if stamps else None


@dataclass
class Dump:
    """One language's dump: its zips and the last modification it holds."""
    language: str
    folder: Path
    trusted_until: datetime
    zips: list[zipfile.ZipFile] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    counts: Counter = field(default_factory=Counter)

    def take(self, item_uri: str, form: str | None, name: str | None, size: int | None,
             tree: dict | None) -> bytes | None:
        """The item's bytes from the dump, or None: fetch it from Cellar
        (the module's rule). Counted by outcome in `counts`."""
        def no(why: str) -> None:
            self.counts[why] += 1
        if not name or size is None or not form or tree is None:
            return no("unlisted")
        uuid = work_uuid(item_uri)
        if uuid is None:
            return no("not-a-cellar-item")
        when = modified(tree, manifestation_of(item_uri))
        if when is None:
            return no("undated")
        if when >= self.trusted_until:
            return no("modified-since")
        member = f"{uuid}/{form}/{name}"
        with self.lock:
            for zf in self.zips:
                try:
                    info = zf.getinfo(member)
                except KeyError:
                    continue
                if info.file_size != size:
                    return no("other-size")
                try:
                    data = zf.read(info)           # its CRC-32 checked
                except (zipfile.BadZipFile, OSError):
                    return no("unreadable")
                self.counts["taken"] += 1
                return data
        return no("not-in-dump")

    def close(self) -> None:
        for zf in self.zips:
            zf.close()
        self.zips = []


def statement(folder: Path) -> tuple[str, datetime] | str:
    """`(language, created)` from the folder's `datadump.json`, or why not."""
    try:
        data = json.loads((folder / STATEMENT).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return f"{folder}: no {STATEMENT} — its creation is unknown, so no item is taken from it"
    except (OSError, ValueError) as e:
        return f"{folder}: {STATEMENT} unreadable ({e})"
    lang = str(data.get("language") or "").strip().lower()
    created = _stamp(data.get("created") or "")
    if len(lang) != 2 or created is None:
        return f"{folder}: {STATEMENT} must state a two-letter `language` and `created` with its zone"
    return lang, created


class Dumps:
    """The dumps under `EURLEX_DATADUMP_DIR`, one language open at a time
    per need (LRU of OPEN_LANGUAGES). Thread-safe."""

    def __init__(self, base: Path | None, log=print) -> None:
        self.base = base
        self.log = log
        self._folders: dict[str, tuple[Path, datetime]] | None = None
        self._open: OrderedDict[str, Dump] = OrderedDict()
        self._lock = threading.Lock()

    def _scan(self) -> dict[str, tuple[Path, datetime]]:
        out: dict[str, tuple[Path, datetime]] = {}
        if self.base is None or not self.base.is_dir():
            return out
        for folder in sorted(p for p in self.base.iterdir() if p.is_dir()):
            got = statement(folder)
            if isinstance(got, str):
                self.log(f"[datadump] {got}")
                continue
            lang, created = got
            if lang in out:
                self.log(f"[datadump] {folder}: a second dump of {lang} ({out[lang][0]} kept)")
                continue
            out[lang] = (folder, created)
        return out

    def for_language(self, lang: str) -> Dump | None:
        with self._lock:
            if self._folders is None:
                self._folders = self._scan()
            if lang in self._open:
                self._open.move_to_end(lang)
                return self._open[lang]
            entry = self._folders.get(lang)
            if entry is None:
                return None
            folder, created = entry
            dump = Dump(language=lang, folder=folder, trusted_until=created - MARGIN)
            for p in sorted(folder.glob("*.zip")):
                try:
                    dump.zips.append(zipfile.ZipFile(p))
                except (zipfile.BadZipFile, OSError) as e:
                    self.log(f"[datadump] {p}: not read ({e}); its items come from Cellar")
            self._open[lang] = dump
            while len(self._open) > OPEN_LANGUAGES:
                _, old = self._open.popitem(last=False)
                with old.lock:
                    old.close()
            return dump


_DUMPS: Dumps | None = None
_DUMPS_LOCK = threading.Lock()


def dumps() -> Dumps:
    """The process's dumps, from `EURLEX_DATADUMP_DIR` (unset: none)."""
    global _DUMPS
    with _DUMPS_LOCK:
        if _DUMPS is None:
            raw = os.environ.get(ENV, "").strip()
            _DUMPS = Dumps(Path(raw) if raw else None)
        return _DUMPS
