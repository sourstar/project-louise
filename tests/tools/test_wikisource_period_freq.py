"""tools/lexicon/wikisource_period_freq.py: the work's date from its own
header, subpages inheriting it, the form as printed (ſ as s, accents and
elision kept), and louise-ocr's TSV with its LICENCE byte-exact."""
from __future__ import annotations

import hashlib
from pathlib import Path

from tools.lexicon import wikisource_period_freq as w


def test_the_year_comes_from_the_header_and_a_century_from_its_name():
    assert w.year_of("Niccolò Machiavelli\n1532\nstoria\n") == 1532
    assert w.year_of("Anonimo\nXIV secolo\npoesia\n") == 1350
    assert w.year_of("Autore\n1628-1650\nteatro\n") == 1628
    assert w.year_of("Capitolo primo\n../Capitolo II\nIncludiIntestazione\n") is None


def test_forms_keep_accents_and_elision_and_write_long_s_as_s():
    words = w.WORD.findall("Perché l'huomo ſi moſtra de' fatti, 1534.")
    assert [w.form_of(x) for x in words] == ["perché", "l'", "huomo", "si", "mostra", "de'", "fatti"]


def test_subpages_inherit_the_root_year_and_undated_works_are_left_out(tmp_path: Path):
    titles = ["Opera", "Opera/Capitolo I", "Senza data", "Moderna"]
    texts = ["Autore\n1550\nprosa\nhauea", "IncludiIntestazione\nhauea fatto", "Nessuno\nprosa\nparola", "A\n1950\nx\noggi"]
    c = w.count_texts([(1500, "hauea hauea")])
    assert c[("hauea", 1500)] == 2
    counts = w.frequencies(titles, texts, 1200, 1899, procs=1)
    assert counts[("hauea", 1500)] == 2 and counts[("fatto", 1500)] == 1
    assert not any(f in ("parola", "oggi") for f, _ in counts)


def test_the_tsv_is_louise_ocrs_format_and_the_licence_is_byte_exact(tmp_path: Path):
    n = w.write(w.count_texts([(1500, "e e il"), (1300, "e")]), tmp_path / "f.tsv", "wikisource-it-test")
    lines = (tmp_path / "f.tsv").read_text(encoding="utf-8").splitlines()
    assert n == 3 and lines[0] == "form\tcount\tcentury\tsource"
    assert lines[1:] == ["e\t1\t1300\twikisource-it-test", "e\t2\t1500\twikisource-it-test", "il\t1\t1500\twikisource-it-test"]
    w.write_terms(tmp_path, "wikisource-it-test", Path("it.parquet"))
    assert hashlib.sha256((tmp_path / "LICENCE").read_bytes()).hexdigest() == \
        "3f941b3b89cf7b8370ceb83cc76d2120d471b58735d8ca60238a751a48d7f72f"
    assert (tmp_path / "SOURCES").read_text(encoding="utf-8").startswith("wikisource-it-test\thttps://huggingface.co/")


def test_scan_pages_count_by_the_print_year_of_their_edition_and_only_proofread(tmp_path: Path):
    import bz2
    page = lambda ns, title, text: (f"<page><title>{title}</title><ns>{ns}</ns><revision><text>{text}</text>"
                                    "</revision></page>")
    q = lambda lv: f'&lt;noinclude&gt;&lt;pagequality level="{lv}" user="x" /&gt;&lt;/noinclude&gt;'
    xml = ('<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">'
           + page(110, "Indice:Rime.djvu", "{{:MediaWiki:Proofreadpage_index_template\n|Anno=1554\n}}")
           + page(110, "Indice:Moderna.djvu", "|Anno=1912\n")
           + page(108, "Pagina:Rime.djvu/1", q(4) + "L&apos;huomo vtile")
           + page(108, "Pagina:Rime.djvu/2", q(1) + "nonriletta")
           + page(108, "Pagina:Moderna.djvu/1", q(4) + "uomo")
           + page(0, "Rime", "testo del namespace principale")
           + "</mediawiki>").encode()
    dump = tmp_path / "it-pages-articles.xml.bz2"
    dump.write_bytes(bz2.compress(xml))
    years = w.print_years(w._pages(dump))
    assert years == {"Rime.djvu": 1554, "Moderna.djvu": 1912}
    counts = w.count_all(w.scan_page_texts(w._pages(dump), years, 1200, 1899, 3), procs=1)
    assert counts == {("l'", 1500): 1, ("huomo", 1500): 1, ("vtile", 1500): 1}
    w.write_terms(tmp_path, "wikisource-it-pages-test", dump)
    assert "print year of their edition" in (tmp_path / "SOURCES").read_text(encoding="utf-8")
