"""THE CREDITS a result and a card carry (operator, 2026-11-16: "I accept the
European Commission reuse notice, add the credits if not already"; 05 §5.3
`credits`). Where a licence makes the source's acknowledgement a condition —
CC BY, the Commission's reuse notice — whoever reads the text must be told
whom it is from, beside it, not in a Codex they never open.

Of a collection: its Codex's `corpus` credit (02 §2.7, the plugin's own
`corpus_attribution`, copied at creation and inside `collection_id`), then
each archive's whose vectors it serves. A CREDIT FOLLOWS WHAT IS SHOWN: a
reference's (EuroVoc's labels, app/refs/eurovoc.py `ATTRIBUTION`) goes with
a fact it gives where that fact is shown at ask time, never on every result
of a collection (CLAUDE.md, "A collection holds only the information
contained in its corpus", 2026-11-16).
"""
from __future__ import annotations


def of(collection) -> list[str]:
    """The credit lines, in order, each once; [] for a collection without a
    Codex or a credit (a test's)."""
    out: list[str] = []
    codex = getattr(collection, "codex", None)
    for c in getattr(codex, "credits", None) or ():
        line = str(getattr(c, "attribution", "") or "").strip()
        if line and getattr(c, "kind", None) in ("corpus", "archive") and line not in out:
            out.append(line)
    return out
