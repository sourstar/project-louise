"""ONE UNIT, ONE DOCUMENT — the repair of a collection already holding a unit
twice (2026-11-21: EUR-Lex5, 37,695 units. A unit re-formed into a Formex
package took its native id from its wrapper's OJ day, its old row had the
tree's work day, and discovery never retired a row its unit no longer
yields). Discovery now retires such a row when the new id appears
(app/discovery.py, the plugin's `superseded_ids`); this pass repairs the
rows held before that.

For every locator two live rows share, the plugin's `ref_of_unit` names the
id the unit has NOW; every other row there is retired exactly as discovery
retires one: out of the text index, the vectors and the catalogue
(`unindexable.withdraw_many`, BEFORE the marks commit), its validity row
forgotten and its Work's chain recomputed, its marks reset and its error the
reason. The row stays a tombstone (02 §2.2: a part's line may name it). A
dry run counts; the caller holds the collection's write lock (the worker's
text-index writers are its own)."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

#: the reason a retired row carries (discovery writes the same)
REASON = "superseded by {keep}: the same unit under another id (one unit, one document)"


def plan(con, plugin, log: Callable[[str], None] = print) -> tuple[dict[str, int], list[tuple[str, str]]]:
    """(counts, [(the uid to retire, the uid its unit has now)]) — read only."""
    unit_of = getattr(plugin, "ref_of_unit", None)
    if unit_of is None:
        raise ValueError("the plugin names no unit's id (`ref_of_unit`): nothing to decide by")
    counts = {"rows": 0, "shared_units": 0, "retire": 0, "unresolved": 0}
    retire: list[tuple[str, str]] = []
    by_path: dict[str, list[str]] = defaultdict(list)
    for uid, path, error in con.execute("SELECT uid, path, error FROM documents"):
        counts["rows"] += 1
        if path and not str(error or "").startswith("superseded"):
            by_path[path].append(uid)
    for path, uids in by_path.items():
        if len(uids) < 2:
            continue
        counts["shared_units"] += 1
        ref = unit_of(Path(path))
        keep = f"{ref.source_id}:{ref.native_id}" if ref is not None else None
        if keep is None or keep not in uids:
            counts["unresolved"] += 1               # the unit names none of them: left, said
            if counts["unresolved"] <= 5:
                log(f"[identity] {path} holds {', '.join(sorted(uids))}; the unit now is "
                    f"{keep or 'not whole'}: left as it is")
            continue
        retire += [(u, keep) for u in sorted(uids) if u != keep]
    counts["retire"] = len(retire)
    return counts, retire


def apply(con, collection, retire: list[tuple[str, str]], *, window: int = 500) -> int:
    """Each one out of every index FIRST (app/unindexable.py's order: a stop
    before the tombstones commit leaves a row the next run retires), then its
    validity row forgotten (its Work's chain recomputed), its marks reset and
    its error the reason, a window a transaction. Returns how many."""
    from app import unindexable, validity
    from .derived_check import _begin
    done = 0
    for i in range(0, len(retire), window):
        part = retire[i:i + window]
        unindexable.withdraw_many(con, collection, [u for u, _k in part])
        _begin(con, None)
        for uid, keep in part:
            validity.forget(con, uid)
            con.execute("UPDATE documents SET error=?, rdf_done=0, fts_done=0, embed_done=0, "
                        "summary_done=0 WHERE uid=?", (REASON.format(keep=keep), uid))
        con.execute("COMMIT")
        done += len(part)
    return done


def retire_superseded(root, name: str, *, apply_it: bool = False, window: int = 500,
                      log: Callable[[str], None] = print) -> dict[str, Any]:
    from app import db
    from . import collection as col, records as rec
    root = Path(root)
    name, codex = col.resolve(root, name)
    coll = rec.local_collection(root, name, codex)
    with closing(db.connect(coll)) as con:
        counts, retire = plan(con, coll.plugin_instance(), log=log)
        counts["retired"] = apply(con, coll, retire, window=window) if apply_it else 0
    log(f"[identity] {name}: {counts['rows']} rows, {counts['shared_units']} unit(s) held more than once; "
        f"{'retired' if apply_it else 'would retire'} {counts['retire']}, unresolved {counts['unresolved']}")
    return {"collection": name, **counts, "applied": apply_it}
