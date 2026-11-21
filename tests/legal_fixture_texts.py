"""The legal fixtures as the chunker reads them: `(lang, uid, title,
sections)` of every real document the four legal plugins' tests hold —
Normattiva's AKN members (it), LEGI's files (fr), NeuRIS's LegalDocML.de
(de), EUR-Lex's Formex (en) — each read by its plugin's own reader. For the
legal chunker's golden and identity tests (tests/test_chunking_legal_v4.py):
the texts the shared legal space cuts, in the four languages it embeds."""
from __future__ import annotations

from pathlib import Path

FIX = Path(__file__).parent / "fixtures"


def normattiva() -> list[tuple[str, str, str, list[tuple[str, str]]]]:
    from app.plugins.normattiva import akn, rendition
    out = []
    for f in sorted((FIX / "normattiva").glob("*.xml")):
        xml, _ = akn.decode_member(f.read_bytes())
        act = akn.parse(xml)
        out.append(("it", f"normattiva:{f.stem}", rendition.denomination(act), rendition.sections(act)))
    return out


def legi(tmp: Path) -> list[tuple[str, str, str, list[tuple[str, str]]]]:
    from app.plugins.base import DiscoverState
    from app.plugins.legi import SOURCE
    from tests.legi_fixtures import REAL, tarball
    root = tmp / "legi"
    tarball(root / "Freemium_legi_global_20250713-140000.tar.gz", REAL)
    out = []
    for r in SOURCE.discover(DiscoverState(corpus_root=root)):
        if r.error is None and r.scope == "document":
            doc = SOURCE.parse(SOURCE.fetch(r))
            out.append(("fr", doc.uid, doc.title, list(doc.sections)))
    return sorted(out, key=lambda d: d[1])


def neuris(tmp: Path) -> list[tuple[str, str, str, list[tuple[str, str]]]]:
    from app import plugin_io
    from app.plugins.base import DiscoverState
    from app.plugins.neuris import SOURCE
    from tests import neuris_support as ns
    root = ns.corpus(tmp / "neuris")
    out = []
    for r in SOURCE.discover(DiscoverState(corpus_root=root)):
        if r.error is None:
            doc = plugin_io.canonical_doc(SOURCE, {"native_id": r.native_id, "path": r.locator})
            out.append(("de", doc.uid, doc.title, list(doc.sections)))
    return sorted(out, key=lambda d: d[1])


def eurlex(tmp: Path) -> list[tuple[str, str, str, list[tuple[str, str]]]]:
    from app import plugin_io
    from app.plugins.base import DiscoverState
    from app.plugins.eurlex import SOURCE
    from tests.test_eurlex import build_corpus
    root = build_corpus(tmp / "eurlex")
    out = []
    for r in SOURCE.discover(DiscoverState(corpus_root=root)):
        if r.error is None:
            doc = plugin_io.canonical_doc(SOURCE, {"native_id": r.native_id, "path": r.locator})
            out.append(("en", doc.uid, doc.title, list(doc.sections)))
    return sorted(out, key=lambda d: d[1])


def every(tmp: Path) -> list[tuple[str, str, str, list[tuple[str, str]]]]:
    """Every fixture document of the four, in a fixed order."""
    return [*normattiva(), *legi(tmp), *neuris(tmp), *eurlex(tmp)]
