"""THE BUILD'S CHECKS — is this collection MADE as the platform asks?

The operator, 2026-11-25: "a consistency check shall be available also for
whom is willing to create a compliant collection"; and, the same day, "Every
problem encountered with a collection must become a lesson learned reflected
in RAGE". A lesson that can be CHECKED becomes one of these, so that
"compliant" is something a plugin's author can test against, on their own
collection, with one command: `louise hold check <collection> --build`.

TWO FAMILIES, kept apart:
- THE PLAN'S checks (03 §3.2, app/hold/check.py) are about a COPY — its
  records against its roots, its origin, its space. They are named in the
  Codex, attested, and a peer runs them on what it received.
- THE BUILD'S checks, here, are about how the collection was MADE on this
  node: its manifest, as its plugin's scan and parse left it. They are in no
  Codex and are never attested — nothing of them is on the wire — and they
  need the manifest, so they are the builder's, not a receiving peer's.

EACH CHECK reads only (a manifest opened read-only: never the write lock,
never a created file), costs one pass over the manifest at most, and answers
in plain words: what it found, and for a failure or a check that could not
run, what would supply what is missing (RULE 2). Three outcomes, as the
plan's: pass, fail, INCONCLUSIVE — a check that cannot judge says why and is
neither.

docs/notes/compliance-check.md holds the design, the checks still to build
and which lesson each one is.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Callable

PASS, FAIL, INCONCLUSIVE = "pass", "fail", "inconclusive"
#: how many offending rows a verdict names (the rest are counted)
NAMED = 10


@dataclass(frozen=True)
class Verdict:
    check: str
    result: str
    #: one sentence, in plain words
    says: str
    #: a failure's or an inconclusive's remedy: what would make it pass or run
    supplies: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def shown(self) -> dict[str, Any]:
        return {"check": self.check, "result": self.result, "says": self.says,
                **({"supplies": self.supplies} if self.supplies else {}),
                **({"detail": self.detail} if self.detail else {})}


def check_units(con, plugin) -> Verdict:
    """ONE UNIT, ONE LIVE ROW (the lessons, B1–B3; EUR-Lex5 held 37,695 and
    then 405 units twice, repaired by hand): no locator of the corpus is held
    by two rows that are both live. For a plugin whose unit is one document —
    it names a unit's id (`ref_of_unit`); a container that holds many
    documents (a PubMed file, a Wikipedia dump) shares its locator by design
    and is not judged here."""
    if getattr(plugin, "ref_of_unit", None) is None:
        return Verdict("units", INCONCLUSIVE,
                       "this plugin's locator is a container of many documents, or it names no unit's id: "
                       "rows sharing a locator are not a fault here",
                       supplies="a plugin whose unit is ONE document declares `ref_of_unit(path)`; "
                                "the check then holds it to one live row a unit")
    from app.hold import identity
    notes: list[str] = []
    counts, retire = identity.plan(con, plugin, log=notes.append)
    if not counts["shared_units"]:
        return Verdict("units", PASS, f"{counts['rows']:,} rows, no unit held twice", detail=counts)
    return Verdict(
        "units", FAIL,
        f"{counts['shared_units']:,} unit(s) are held by more than one live row "
        f"({counts['retire']:,} row(s) to retire, {counts['unresolved']:,} the unit itself does not settle)",
        supplies="the scan must retire a unit's earlier row when its id moves (the plugin's "
                 "`superseded_ids` names every id a unit ever had); what is held twice now is repaired "
                 "by `louise dev retire-superseded <collection>` (a dry run first)",
        detail={**counts, "first": [{"retire": a, "keep": b} for a, b in retire[:NAMED]], "notes": notes[:NAMED]})


#: a document indexed, in scope, not in error, holding no chunk
CHUNKLESS_SQL = ("SELECT d.uid FROM documents d WHERE d.fts_done=1 AND d.in_scope=1 AND d.error IS NULL "
                 "AND COALESCE(d.chunks, 0)=0")


def check_chunkless(con, plugin) -> Verdict:
    """A DOCUMENT WITH NO CHUNK SAYS WHY (the lessons, E1; 57 of EUR-Lex5's
    were told apart by hand: 16 scans with no text layer, 41 acts whose
    source holds only a title): every document indexed with no chunk carries
    an ISSUE that states the reason (app/issues.py; a plugin declares its
    codes). One that states none is either a text the parse lost or a reason
    nobody wrote down — and the two cannot be told apart from outside."""
    from app import issues
    try:
        uids = [r[0] for r in con.execute(CHUNKLESS_SQL)]
    except Exception as e:   # noqa: BLE001 — a manifest of another shape: said, not judged
        return Verdict("chunkless", INCONCLUSIVE, f"the manifest does not read ({type(e).__name__}: {e})",
                       supplies="a manifest of this platform's schema (`louise dev migrate schema`)")
    if not uids:
        return Verdict("chunkless", PASS, "no document is indexed with no chunk")
    stated: dict[str, list[str]] = {}
    for i in range(0, len(uids), 900):
        part = uids[i:i + 900]
        marks = ",".join("?" * len(part))
        try:
            for uid, code in con.execute(f"SELECT uid, code FROM issues WHERE uid IN ({marks})", part):
                stated.setdefault(uid, []).append(code)
        except Exception:   # noqa: BLE001 — no issues table: nothing is stated
            break
    by_reason: dict[str, int] = {}
    for codes in stated.values():
        key = "+".join(sorted(set(codes)))
        by_reason[key] = by_reason.get(key, 0) + 1
    silent = [u for u in uids if u not in stated]
    detail = {"indexed_with_no_chunk": len(uids), "by_reason": dict(sorted(by_reason.items())),
              "with_no_reason": len(silent), "first": heapq.nsmallest(NAMED, silent),
              "codes_declared": sorted(issues.declared(plugin))}
    if not silent:
        return Verdict("chunkless", PASS,
                       f"{len(uids):,} document(s) are indexed with no chunk, each with its stated reason",
                       detail=detail)
    return Verdict(
        "chunkless", FAIL,
        f"{len(silent):,} of {len(uids):,} document(s) indexed with no chunk state no reason",
        supplies="the plugin's parse reports an issue for a document that legitimately has no text "
                 "(`ISSUES` declares the code: `no-text-layer`, `title-only`, a redirect…); a document "
                 "that HOLDS text and gave no chunk is a parse to fix",
        detail=detail)


#: name -> the check, in the order they are run and shown
CHECKS: dict[str, Callable[[Any, Any], Verdict]] = {
    "units": check_units,
    "chunkless": check_chunkless,
}


def run(collection, checks: list[str] | None = None) -> list[Verdict]:
    """The build's checks of one collection this node holds the manifest of.
    Reads only. ValueError for a check that does not exist."""
    from app import issues
    want = list(checks) if checks else list(CHECKS)
    unknown = [c for c in want if c not in CHECKS]
    if unknown:
        raise ValueError(f"{', '.join(unknown)}: no such build check; they are {', '.join(CHECKS)}")
    con = issues.open_read(collection.manifest_path)
    if con is None:
        return [Verdict(c, INCONCLUSIVE, "this node holds no manifest of the collection: nothing was built here",
                        supplies="the build's checks run where the collection is made (a scan has run); a copy "
                                 "received from a peer is checked by its plan: `louise hold check <collection>`")
                for c in want]
    try:
        plugin = collection.plugin_instance()
        return [CHECKS[c](con, plugin) for c in want]
    finally:
        con.close()
