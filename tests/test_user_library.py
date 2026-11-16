"""The users' Library (operator, 2026-11-16): "Web gui improvement: user
account, add the Library tab with only the read only collections cards. Each
collection card shows only the top information up to the "embedded" bar
included, but with no ETA information."

The card's top is ONE fragment (templates/_fragments/collection_card_top.html)
that the admin Library includes too, so the two cannot drift."""
import re
from html.parser import HTMLParser

import pytest

from app import collections as collections_mod, db, users, web

from tests.test_library_card_eta import _render, _seed
from tests.test_users import client  # noqa: F401 — the fixture


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _tags(html: str) -> list[tuple[str, dict]]:
    p = _Tags()
    p.feed(html)
    return p.tags


def _main(html: str) -> str:
    return html[html.index("<main>"):html.index("</main>")]


def _with_etas(shelf: dict) -> dict:
    """A shelf whose every stage has an ETA, and its dense row best and current."""
    s = dict(shelf)
    s["stage_etas"] = {k: "~7min" for k in ("fetch", "rdf", "fts", "embed")}
    s["stage_eta_why"] = {k: "+3 waiting" for k in ("fetch", "rdf", "fts", "embed")}
    s["embed_eta"] = {"best": "~5min", "current": "~9min", "rate": "3.0 chunks/s",
                      "current_why": "at the rate measured now"}
    return s


def _render_user(shelf, worker_state="running"):
    return web.TEMPLATES.env.get_template("user_library.html").render(
        request=None, active="library", shelves=[shelf], worker_state=worker_state,
        readonly=True)


@pytest.fixture
def shelf(isolated_config):
    c = collections_mod.default_collection()
    con = db.connect(c)
    _seed(con, [(1, 1, 1, 1, None)] * 3 + [(1, 1, 0, 0, None)] * 7)
    web.invalidate_counts()
    return web._collection_shelf(c, {}, "running")


def test_a_logged_in_user_gets_the_cards_and_the_tab(client, shelf):  # noqa: F811
    _, token = users.add("ada")
    assert client.post("/login", json={"token": token}).json()["role"] == "user"
    page = client.get("/library", headers={"accept": "text/html"})
    assert page.status_code == 200
    names = [c.name for c in collections_mod.load_public()]
    assert names and all(f'data-collection="{n}"' in page.text for n in names), "one card each, as Ask lists them"
    assert '<a href="/library" class="active">Library</a>' in page.text, "the user's tab"
    assert "/admin/library" not in page.text, "never the admin's"
    main = _main(page.text)
    assert ">embedded</span>" in main and ">indexed</span>" in main and ">parsed</span>" in main
    for tag, attrs in _tags(main):
        assert tag not in ("details", "button", "select", "form", "textarea"), tag
        assert not (tag == "input"), "no control of any kind"
        assert not any(k.startswith("on") or k.startswith("hx-") for k in attrs), (tag, attrs)
        assert tag != "a", "no link that changes anything"


def test_anonymous_is_sent_to_the_login_like_ask(client):  # noqa: F811
    users.add("ada")
    r = client.get("/library", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/library"
    assert client.get("/library", headers={"accept": "application/json"}).status_code == 401


def test_the_admin_keeps_its_library_tab(client):  # noqa: F811
    _, token = users.add("root-two", role="admin")
    page = client.get("/ask", headers={"Authorization": f"Bearer {token}"})
    assert '<a href="/admin/library"' in page.text and 'href="/library"' not in page.text


def test_each_card_ends_at_the_embedded_bar(shelf):
    html = _render_user(_with_etas(shelf))
    cards = re.findall(r'<div class="panel" data-collection="[^"]*">(.*?)\n</div>\n', html, re.S)
    assert len(cards) == 1
    card = cards[0]
    tail = card[card.index(">embedded</span>"):]
    assert 'class="spine"' in tail
    # after the dense bar: its stage's closing tag, nothing more
    after = tail[tail.index('class="spine"'):]
    assert re.fullmatch(r'class="spine">.*?</div>\s*</div>\s*', after, re.S), after[:400]
    for word in ("Details", "Options", "Embed mode", "waiting", "blocked", "clear errors"):
        assert word not in re.sub(r"<[^>]+>", "", card), word


def test_no_eta_anywhere_on_the_users_card(shelf):
    s = _with_etas(shelf)
    admin = _render(s)
    assert 'data-eta="embed"' in admin and "~7min" in admin, "the admin's card has them"
    user = _render_user(s)
    for gone in ('data-eta', "ETA", "~7min", "~5min", "~9min", "chunks/s", "+3 waiting", "Options ›"):
        assert gone not in user, gone


def test_the_admin_card_top_is_the_same_fragment(shelf):
    """Both pages render the card's top from one fragment: the user's card is
    the admin's, its ETAs and Options pointers left out."""
    s = {**shelf, "embed_eta": None,
         "stage_etas": {k: None for k in shelf["stage_etas"]},
         "stage_eta_why": {k: None for k in shelf["stage_eta_why"]}}
    user = _render_user(s)
    admin = _render(s)
    top = re.search(r'<div class="panel" data-collection="[^"]*">\n(.*?)\n</div>\n', user, re.S).group(1)
    assert "Options ›" in admin and "Options ›" not in top
    body = top[top.index("</h2>\n"):]   # the name's tooltip is the admin's alone
    assert body in admin, "where nothing differs (no ETA here), the same bytes"
