"""Reviewer probe (rvC1, architecture, 2026-11-26): what each record SHOULD
be told, LCSH/shelf cases the 28 fixtures lack. Each case names the real PG
record it stands for (pg_catalog.csv, 2026-11-26) where there is one. Kept
as the reviewer wrote it."""
from __future__ import annotations
import pytest
from app import retrieval
LIT, OPN = "a literary work", "a work of opinion or belief"

def verdict(subjects: str, shelves: str = "") -> str | None:
    said = retrieval._work_said({"subjects": subjects, "bookshelves": shelves})
    return LIT if f" — {LIT} (" in said else OPN if f" — {OPN} (" in said else None

CASES = [
    ("bible", "Bible | BS", "Category: Religion/Spirituality", None),
    ("koran", "Qur'an | BP", "Category: Religion/Spirituality", None),
    ("koran commentary, no shelf", "Koran -- Commentaries | BP", "", None),
    ("tipitaka paraphrase (#36039)", "Tipitaka. Suttapitaka. Khuddakanikaya. Jataka -- Paraphrases, English | BQ",
     "Category: Short Stories | Category: Religion/Spirituality", None),
    ("mishnah (#8548 Pirke Avot)", "Mishnah. Avot | BM", "Category: Philosophy & Ethics | Category: Religion/Spirituality", None),
    ("bhagavadgita uniform title", "Bhagavadgita | BL", "Category: Poetry | Category: Religion/Spirituality", None),
    ("bible stories", "Bible stories, English | BS", "Category: Children & Young Adult Reading", None),
    ("book of mormon stories (#51095)", "Book of Mormon stories, English | BX", "Category: Children & Young Adult Reading", None),
    ("jataka stories (#62514)", "Jataka stories, English | BQ", "Category: Children & Young Adult Reading", None),
    ("bible criticism, no shelf", "Bible -- Criticism, interpretation, etc. | BS", "", None),
    ("bible criticism on Philosophy shelf (#45475)", "Bible -- Criticism, interpretation, etc. | BS", "Category: Philosophy & Ethics", OPN),
    ("bible sermon on Essays shelf (#64732)", "Bible -- Sermons | BV", "Category: Essays, Letters & Speeches", OPN),
    ("Huxley, Bible and science (#2631)", "Bible and science | Q", "Category: Essays, Letters & Speeches", OPN),
    ("Ben-Hur (#2145)", "Historical fiction | Jesus Christ -- Fiction | "
     "Bible. New Testament -- History of Biblical events -- Fiction | PS", "Category: Historical Novels", LIT),
    ("Paradise Lost (#26)", "Fall of man -- Poetry | Adam (Biblical figure) -- Poetry | "
     "Bible. Genesis -- History of Biblical events -- Poetry | PR", "Category: Poetry", LIT),
    ("Bible plays (#12794)", "Bible plays | Judith (Biblical figure) -- Drama | PR", "Category: Plays/Films/Dramas", LIT),
    ("novel quoting the Bible (#44748)", "Bible -- Quotations | PS", "Category: Novels", LIT),
    ("Mythology, Greek -- Juvenile fiction", "Mythology, Greek -- Juvenile fiction | PZ", "", LIT),
    ("Fables", "Fables | PZ", "", LIT),
    ("Iliad", "Epic poetry, Greek -- Translations into English | PA", "Category: Classics of Literature", LIT),
    ("historical fiction + biography mix", "Historical fiction | Napoleon I, Emperor of the French, 1769-1821 -- Biography | PS", "", LIT),
    ("Italian poetry", "Italian poetry -- 19th century | PQ", "", LIT),
    ("French drama (Comedy)", "French drama (Comedy) | PQ", "", LIT),
    ("Folklore -- Italy", "Folklore -- Italy | GR", "Category: Mythology, Legends & Folklore", None),
    ("Psalms (Music)", "Psalms (Music) | M", "", None),
    ("Hymns, no shelf", "Hymns, English | BV", "", None),
    ("Sermons, no shelf", "Sermons, American | BV", "", None),
    ("Drama -- History and criticism", "Drama -- History and criticism | PN", "", None),
    ("Communism + Politics shelf", "Communism | HX", "Category: Politics", None),
    ("autobiography on a Novels shelf", "Authors, American -- 19th century -- Biography | PS", "Category: Novels", None),
    ("Sermons on Essays shelf", "Sermons, American | BV", "Category: Essays, Letters & Speeches", OPN),
    ("Republic", "Political science -- Early works to 1800 | JC", "Category: Philosophy & Ethics", OPN),
    ("A Modest Proposal (#1080)", "Political satire, English | Religious satire, English | PR", "Category: Essays, Letters & Speeches", None),
    # architecture's final read, 2026-11-26 — headings shaped as those records' (the reader checked the real ones)
    ("Expositor's Bible: The Psalms (#44027)", "Bible. Psalms -- Commentaries | BS", "Category: Poetry", None),
    ("a Psalms commentary (#30252)", "Bible. Psalms -- Criticism, interpretation, etc. | BS", "Category: Poetry", None),
    ("Cheyne's criticism (#65866)", "Bible. Proverbs -- Criticism, interpretation, etc. | Gnomic poetry, Hebrew | BS",
     "", None),
    ("a study of satire (#29237)", "Satire, English -- History and criticism | PR", "Category: Essays, Letters & Speeches", OPN),
    ("Bible stories, juvenile fiction (#11083)", "Bible stories -- Juvenile fiction | BS", "Category: Children & Young Adult Reading", None),
]

@pytest.mark.parametrize("name,subjects,shelves,want", CASES, ids=[c[0] for c in CASES])
def test_probe(name, subjects, shelves, want):
    assert verdict(subjects, shelves) == want, retrieval._work_said({"subjects": subjects, "bookshelves": shelves})

def test_work_subjects_from_a_non_classified_plugin_get_no_verdict():
    assert retrieval._work_said({"work_subjects": ("Historical fiction",)}) == ""
