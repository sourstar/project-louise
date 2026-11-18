"""The reading reference sources (architecture's ruling, 2026-11-18):
wikisource-readings' pairs, both forms attested, the homograph exclusion
against the modern form; tlio's glosses, held back until the terms are
acknowledged (invariant 9), and the same exclusion."""
from __future__ import annotations

import bz2
import collections
import gzip
from pathlib import Path

from app import licensing
from app.plugins.base import RefSyncState
from app.refs import readings, store as ref_store, tlio, wikisource_readings as wr


def test_variants_undo_the_old_printing_conventions():
    assert "utile" in wr.variants("vtile")
    assert "avere" in wr.variants("hauere")
    assert {"perché", "perchè"} <= wr.variants("perche")
    assert "orazione" in wr.variants("oratione")
    assert "orazione" not in wr.variants("orazione")
    assert "queszione" not in wr.variants("questione")
    assert all("qv" not in v for v in wr.variants("quando"))    # u after q stays


def counts(**kw) -> collections.Counter:
    c = collections.Counter()
    for k, n in kw.items():
        form, century = k.rsplit("_", 1)
        c[(form, int(century))] = n
    return c


def test_a_pair_needs_both_forms_attested_and_is_dropped_where_the_old_form_is_a_modern_word():
    c = counts(huomo_1500=6, huomo_1600=5, uomo_1900=3000, filler_1900=1_000_000,
               hauere_1500=4, avere_1900=2000,
               habito_1500=9, abito_1900=500, habito_1900=200,          # still printed: a 1900s word
               zzqqx_1500=1,                                            # a slip
               vtile_1500=10, utile_1900=900)
    rows, modern, total = wr.derive(c)
    got = {r["orig"]: (r["reg"], r["period_from"], r["period_to"]) for r in rows}
    assert got["huomo"] == ("uomo", 1500, 1699)
    assert got["hauere"][0] == "avere" and got["vtile"][0] == "utile"
    assert "habito" not in got and "zzqqx" not in got
    assert all(r["kind"] == readings.REPLACE and r["method"] == f"wikisource-variants {wr.RULES_VERSION}"
               for r in rows)
    assert total == sum(n for (f, cen), n in c.items() if cen == 1900)


def _dump(tmp_path: Path) -> Path:
    page = lambda ns, title, text: (f"<page><title>{title}</title><ns>{ns}</ns><revision><text>{text}</text>"
                                    "</revision></page>")
    q = '&lt;noinclude&gt;&lt;pagequality level="4" user="x" /&gt;&lt;/noinclude&gt;'
    modern = " ".join(["uomo"] * 50 + ["parola"] * 10)
    xml = ('<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">'
           + page(110, "Indice:Rime.djvu", "|Anno=1554\n") + page(110, "Indice:Oggi.djvu", "|Anno=1952\n")
           + "".join(page(108, f"Pagina:Rime.djvu/{i}", q + "L'huomo") for i in range(10))
           + page(108, "Pagina:Oggi.djvu/1", q + modern) + "</mediawiki>").encode()
    p = tmp_path / "it.xml.bz2"
    p.write_bytes(bz2.compress(xml))
    return p


def test_the_transform_and_sync_give_the_pairs_from_a_dump(tmp_path: Path):
    raw = _dump(tmp_path)
    wr.transform(raw, tmp_path / wr.FINAL, procs=1)
    rows = [r for b in wr.WikisourceReadingsSource().sync(RefSyncState(ref_root=tmp_path)) for r in b.rows]
    assert [(r["orig"], r["reg"], r["period_from"], r["period_to"]) for r in rows] == [("huomo", "uomo", 1500, 1599)]
    modern, total = wr.modern_counts(tmp_path)
    assert modern["uomo"] == 50 and total == 60
    con = ref_store.connect(tmp_path / "refs.db")
    assert ref_store.sync_source(con, wr.WikisourceReadingsSource(), RefSyncState(ref_root=tmp_path))["rows"] == 1
    assert readings.table_digest(con, wr.TABLE.name)


PAGE = ("<html><head><title>ABBONDARE v.</title></head><body>ABBONDARE v. Lista forme Tutto/Stampa "
        "0.1 abonda , abondava , habundare , abbandono , f : abunnare . 0.2 Lat. abundare. "
        "0.3 Uguccione, XIII in.: 1 . 0.7 1 Essere presente in grande quantità. 2 Altro. "
        "0.8 Pietro G. Beltrami 10.03.1998. 1 Essere ... [u.r. 16.09.2009]</body></html>").encode("cp1252")


def _tlio_dir(tmp_path: Path) -> Path:
    base = tmp_path / tlio.DIR
    (base / "raw" / "voci").mkdir(parents=True)
    (base / "raw" / "voci" / "000120.htm.gz").write_bytes(gzip.compress(PAGE))
    (tmp_path / wr.MODERN).write_text("#total\t1000000\nabbandono\t40\n", encoding="utf-8")
    return base


def test_a_tlio_page_parses_into_its_lemma_forms_and_first_definition():
    e = tlio.parse(PAGE)
    assert e["lemma"] == "abbondare" and e["pos"] == "v."
    assert e["forms"] == ["abonda", "abondava", "habundare", "abbandono", "abunnare"]
    assert e["definition"] == "Essere presente in grande quantità."


def test_tlio_is_not_synced_before_its_terms_are_acknowledged(tmp_path: Path):
    base = _tlio_dir(tmp_path)
    state = RefSyncState(ref_root=tmp_path)
    assert list(tlio.TlioSource().sync(state)) == []                    # no terms
    (base / licensing.REF_TERMS).write_bytes(b"TLIO: no licence stated; non-commercial, local.\r\n")
    assert list(tlio.TlioSource().sync(state)) == []                    # terms, no ack
    licensing.make_ref_ack(base, "2026-11-18T00:00:00Z")
    rows = [r for b in tlio.TlioSource().sync(state) for r in b.rows]
    got = {r["orig"]: r for r in rows}
    # abbandono is a 1900s word (40 a million): no gloss
    assert sorted(got) == ["abonda", "abondava", "abunnare", "habundare"]
    r = got["abondava"]
    assert (r["kind"], r["lemma"], r["reg"], r["period_from"], r["period_to"]) == \
        (readings.GLOSS, "abbondare", "", 1100, 1400)
    assert r["gloss"] == "Essere presente in grande quantità."


def test_the_terms_are_hashed_byte_exact(tmp_path: Path):
    base = _tlio_dir(tmp_path)
    (base / licensing.REF_TERMS).write_bytes(b"terms\r\n")
    licensing.make_ref_ack(base)
    assert licensing.ref_held_back(base, "tlio") is None
    (base / licensing.REF_TERMS).write_bytes(b"terms\n")                # converted: not the text acknowledged
    assert "not acknowledged" in licensing.ref_held_back(base, "tlio")


def test_the_derivations_parameters_are_its_version():
    # a change to any of these is a new RULES_VERSION (ref_version): change both together
    assert (wr.RULES_VERSION, wr.MIN_OLD, wr.MIN_LEN, wr.SHORT, wr.MIN_OLD_SHORT, wr.MODERN_PER_MILLION,
            wr.HOMOGRAPH_SHARE, wr.MAX_SITES) == (3, 3, 4, 6, 10, 1.0, 0.05, 8)
    assert wr.WikisourceReadingsSource.ref_version == wr.RULES_VERSION


def test_the_homograph_bar_is_relative_to_the_modern_form():
    # measured on the 20261101 dump, per million 1900s words: perche 15 against
    # perché 751 (reprints of old texts: kept); gioja 16.3 against gioia 127.9 (excluded)
    c = counts(perche_1500=1085, perche_1900=15, **{"perché_1900": 751},
               gioja_1800=400, gioja_1900=163, gioia_1900=1279, filler_1900=1_000_000 - 2208)
    rows, _modern, _total = wr.derive(c)
    got = {r["orig"]: r["reg"] for r in rows}
    assert got.get("perche") == "perché"
    assert "gioja" not in got


def test_short_forms_numerals_cut_words_and_foreign_ys_give_no_pair():
    c = counts(filler_1900=1_000_000, **{"ciò_1900": 900, "così_1900": 800, "ora_1900": 700, "ein_1900": 40,
               "batt_1900": 30, "ciii_1900": 30, "dik_1900": 20, "avea_1900": 300},
               cio_1500=50, cosi_1500=40, hora_1500=2941, hein_1700=4, bapt_1500=9, ciij_1500=9,
               dyk_1700=9, auea_1500=30)
    got = {r["orig"]: r["reg"] for r in wr.derive(c)[0]}
    assert got == {"cio": "ciò", "cosi": "così", "hora": "ora", "auea": "avea"}
    assert "y" not in "".join("".join(v) for v in wr.variants("dyk"))
