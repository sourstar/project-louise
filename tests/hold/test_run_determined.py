"""THE RUN A NEW DATED-DUMP COLLECTION IS BUILT FROM, DETERMINED (RULE 2;
app/hold/origin_run.py `determine`): where the recipe names no files and no
--run is given, (a) the corpus's run when the update pass's completion check
says it is complete, (b) a `.next` re-creation's predecessor's, (c) the
origin's latest complete run — each said in plain words, on stderr and in
the Library's notice; refused only where none can tell, saying what is
missing and what would supply it. The corpus holding two runs is never
guessed at: the newest is taken when complete, which is what the update pass
treats as current, and refused otherwise."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app import louise
from app.acquire import completion
from app.hold import origin_run
from tests.hold import wikidata_runs as wr

ROOT_RUN, NEWER = "20261012", "20261101"


@pytest.fixture(autouse=True)
def _tokenizer():
    from app.plugins.wikidata import chunker
    wr.register_tokenizer()
    yield
    chunker._tokenizers.pop(wr.TOKENIZER, None)


def _run_in(corpus: Path, run: str, *, sums: bool = True, lists_dump: bool = True, sha1: bool = True) -> None:
    """Run `run` as the acquisition leaves it in a corpus: the dump, its
    md5sums (listing the dump, or only a lexemes file) and its sha1sums."""
    from tests.test_plugins_wikidata_source import a_dump
    dump = a_dump(corpus, run)
    d = dump.parent
    md5 = hashlib.md5(dump.read_bytes()).hexdigest()
    if sums:
        line = f"{md5}  {dump.name}\n" if lists_dump else f"{md5}  wikidata-{run}-lexemes.json.bz2\n"
        (d / f"wikidata-{run}-md5sums.txt").write_text(line)
    if sha1:
        (d / f"wikidata-{run}-sha1sums.txt").write_text(f"{'0' * 40}  {dump.name}\n")


def _no_network(*a, **k):
    raise AssertionError("the origin is not asked where the corpus or the predecessor says")


def _determine(root, name, **kw):
    kw.setdefault("fetch_records", _no_network)
    kw.setdefault("fetch_runs", _no_network)
    return origin_run.determine(root, "wikidata", name, **kw)


# ---- (a) the corpus -------------------------------------------------------------

def test_a_the_one_complete_run_the_corpus_holds(tmp_path):
    _run_in(tmp_path / "w" / "origin", ROOT_RUN)
    run, why = _determine(tmp_path, "w")
    assert run == ROOT_RUN and "the run the corpus holds, complete by Wikimedia's own record" in why


def test_a_of_two_runs_the_newest_complete_one_is_what_the_update_takes(tmp_path):
    corpus = tmp_path / "w" / "origin"
    _run_in(corpus, ROOT_RUN)
    _run_in(corpus, NEWER)
    run, why = _determine(tmp_path, "w")
    assert run == NEWER and "the newest of 2 it holds" in why


@pytest.mark.parametrize("how", ["sums list no dump", "a file not fetched yet"])
def test_a_a_newer_run_not_complete_is_never_passed_over_for_an_older(tmp_path, how):
    corpus = tmp_path / "w" / "origin"
    _run_in(corpus, ROOT_RUN)
    if how == "sums list no dump":
        _run_in(corpus, NEWER, lists_dump=False)
        said = "carries no full entity dump"
    else:
        _run_in(corpus, NEWER, sha1=False)
        said = "1 of its files not in the corpus yet"
    with pytest.raises(origin_run.RunUndetermined, match=f"the corpus holds run {NEWER}, not complete") as e:
        _determine(tmp_path, "w")
    assert said in str(e.value) and f"{ROOT_RUN} besides" in str(e.value)
    assert "never take an older run" in str(e.value) and "--run" in str(e.value)


def test_a_one_run_whose_file_is_not_fetched_yet_is_refused_and_the_origin_not_asked(tmp_path):
    _run_in(tmp_path / "w" / "origin", ROOT_RUN, sums=False)
    with pytest.raises(origin_run.RunUndetermined, match=f"the corpus holds run {ROOT_RUN}, not complete "
                                                         f"\\(1 of its files not in the corpus yet"):
        _determine(tmp_path, "w")


# ---- (b) the predecessor ----------------------------------------------------------

def test_b_a_re_creation_takes_its_predecessors_run(data_dir, config_dir, isolated_config):
    root, _corpus, _live = wr.live(data_dir, config_dir)
    run, why = _determine(root, "wikidata.next")
    assert run == ROOT_RUN and "collections/wikidata.toml `snapshot`" in why


# ---- (c) the origin ---------------------------------------------------------------

def _origin(listing: dict[str, bytes | None]):
    """An origin whose index lists `listing`'s runs, newest first, each
    md5sums as given (None: not published yet)."""
    def runs(spec):
        return tuple(sorted(listing, reverse=True))

    def records(spec, recs):
        return {r.rel_path: listing[r.rel_path.split("/")[-2]] for r in recs}
    return records, runs


def test_c_the_origins_latest_complete_run_past_one_still_written_and_one_of_lexemes(tmp_path):
    records, runs = _origin({
        "20261201": b"0123456789abcdef0123456789abcdef  wikidata-20261201-all.json.gz\n",                   # writing the .bz2 yet
        "20261125": b"0123456789abcdef0123456789abcdef  wikidata-20261125-lexemes.json.bz2\n",               # lexemes only: never carries it
        "20261115": b"0123456789abcdef0123456789abcdef  wikidata-20261115-all.json.bz2\n",
        "20261101": b"0123456789abcdef0123456789abcdef  wikidata-20261101-all.json.bz2\n"})
    run, why = _determine(tmp_path, "fresh", fetch_records=records, fetch_runs=runs)
    assert run == "20261115" and why == "Wikimedia's latest complete run (20261201 newer, not complete yet)"


def test_c_no_complete_run_at_the_origin_is_refused_saying_what_would_supply_it(tmp_path):
    records, runs = _origin({"20261201": None})
    with pytest.raises(origin_run.RunUndetermined, match="lists no complete run .* fetch a run into the corpus, "
                                                         "or give --run"):
        _determine(tmp_path, "fresh", fetch_records=records, fetch_runs=runs)


def test_c_an_origin_away_is_a_deferral(tmp_path):
    def away(spec):
        raise completion.OriginAway("timed out")
    with pytest.raises(ConnectionError, match="did not answer"):
        _determine(tmp_path, "fresh", fetch_runs=away)


# ---- said: on stderr, and in the Library's notice -----------------------------------

def test_the_run_taken_is_said_on_stderr_and_to_the_library(tmp_path, capsys):
    _run_in(tmp_path / "w" / "origin", ROOT_RUN)
    heard = []
    assert louise._run_for(tmp_path, "wikidata", None, None, "w", say=heard.append) == ROOT_RUN
    assert heard and heard[0].startswith(f"run: {ROOT_RUN} — the run the corpus holds")
    assert heard[0] in capsys.readouterr().err
    assert louise._run_for(tmp_path, "wikidata", None, "20261101", "w", say=heard.append) == "20261101"
    assert len(heard) == 1, "--run is the operator's: nothing determined, nothing said"


def test_the_librarys_create_carries_the_notice(monkeypatch):
    from app import jobs

    def create_from(root, plugin, recipe, *, say=None, **k):
        say("run: 20261012 — the run the corpus holds")
        return type("C", (), {"collection_id": "sha256:" + "aa" * 32})(), Path("/x/w.toml")
    monkeypatch.setattr(louise, "create_from", create_from)
    out = jobs._create({"recipe": {}, "plugin": "wikidata", "name": "w"})
    assert out["notice"] == "run: 20261012 — the run the corpus holds"


def test_a_create_none_can_tell_is_refused_in_plain_words(data_dir, config_dir, isolated_config, tmp_path,
                                                          monkeypatch, capsys):
    root, _corpus, _live = wr.live(data_dir, config_dir)
    p = wr.whole_recipe(root, tmp_path)                       # the origin lists no run (tests/conftest.py)
    assert louise.main(wr.create_args(root, p, "other")) == 2
    err = capsys.readouterr().err
    assert "names the FIRST run" in err and "lists no complete run" in err and "--run" in err
    assert not (root / "other").exists(), "nothing created"
