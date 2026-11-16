"""THE CREDITS a result and a card carry (operator, 2026-11-16: "I accept the
European Commission reuse notice, add the credits if not already"; 05 §5.3
`credits`). Where a licence makes the source's acknowledgement a condition —
CC BY, the Commission's reuse notice — whoever reads the text must be told
whom it is from, beside it, not in a Codex they never open.

Of a collection: its Codex's `corpus` credit (02 §2.7, the plugin's own
`corpus_attribution`, copied at creation and inside `collection_id`), then
each archive's whose vectors it serves; and for an EUR-Lex collection the
vocabularies its subjects and authors are labelled by (app/refs/eurovoc.py),
whose labels the records name by id (parser 4) or carry as the notice
decoded them (parser 3) — EuroVoc's either way.
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
    if getattr(collection, "plugin", None) == "eurlex":
        from .refs import eurovoc
        out.append(eurovoc.ATTRIBUTION)
    return out
