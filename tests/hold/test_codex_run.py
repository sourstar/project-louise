"""A DATED-DUMP CODEX NAMES ITS RUN (09 §9.17; München, 2026-11-17).

The parser-3 re-creation of wikidata ran the runbook's steps 1-5 and its
check was INCONCLUSIVE: "an origin naming an entity is read in a run, and
none was given". The live Codex had been made from a recipe whose `[origin]`
block carried no `spec`, so `origin.spec = {"files": null}`: no `delta.mode`,
no run — `origin_run.is_dated` False, `current_run` None — and the parser-3
Codex, the live one with its parser and image changed, carried the same, so
its snapshot named no run to read its entities in: permanently unverifiable
against the source (invariant 11).

Here: the creation path pins a dated-dump plugin's run (template, the whole
Codex, `--run`, a re-creation's predecessor); nothing creates, carries into
or freezes a Codex whose records name a keyed unit and which names no run;
and the re-creation by the book, from the live parser-2 collection whose
file names the run, carries into the corrected Codex and freezes a snapshot
naming its run."""
from __future__ import annotations

import json

import pytest

from app import louise
from app.hold import carry as carry_mod
from app.hold import collection as col
from app.hold import line_carry, origin_run, template
from app.hold import records as rec
from app.hold import snapshot as sn
from app.hold.codex import CodexError
from tests.hold import wikidata_runs as wr
from tests.hold.conftest import KEY, NOW, PLATFORM, fake_embed, fake_query_embed

RUN = wr.RUN
FILES = [f"wikidatawiki/entities/{RUN}/wikidata-{RUN}-{x}" for x in ("all.json.bz2", "md5sums.txt", "sha1sums.txt")]


@pytest.fixture(autouse=True)
def _tokenizer():
    from app.plugins.wikidata import chunker
    wr.register_tokenizer()
    yield
    chunker._tokenizers.pop(wr.TOKENIZER, None)


def _create(root, recipe, *, name, run=None, plugin="wikidata"):
    return col.create_from_recipe(root, plugin, recipe, platform_image=PLATFORM, embed=fake_embed,
                                  embed_query=fake_query_embed, name=name, key=KEY, now=NOW, run=run)


# ---- the creation path -------------------------------------------------------

def test_the_template_pins_a_dated_plugins_run(tmp_path):
    """`--run` writes delta, filters and the run's files; the recipe as
    docs/notes/wikidata-recipe.toml writes it, with no run, is refused."""
    with pytest.raises(template.TemplateError, match="names the FIRST run"):
        _create(tmp_path, wr.recipe(), name="w")
    assert not (tmp_path / "w").exists(), "nothing created"
    c = _create(tmp_path, wr.recipe(), name="w", run=RUN)
    spec = c.origin.spec
    assert spec["delta"] == {"mode": "dated-dumps", "subdir": None}
    assert [f["rel_path"] for f in spec["files"]] == FILES
    assert all(f["size"] is None and f["checksum"] is None for f in spec["files"])
    assert spec["filters"] == ["*-all.json.bz2", "*-md5sums.txt", "*-sha1sums.txt"]
    assert origin_run.is_dated(c) and origin_run.codex_run(c) == RUN
    # THE WHOLE IDENTIFYING HALF (09 §9.17 "in the Codex: yes"), in wikipedia's form
    assert {k: v for k, v in spec.items() if k not in ("files", "filters")} == {
        "source_id": "wikidata", "transport": "https", "url": "https://dumps.wikimedia.org",
        "layout": "single-file", "listing": "snapshot-index",
        "checksums": {"kind": "none", "suffix": "", "algo": ""},
        "delta": {"mode": "dated-dumps", "subdir": None}}
    # the same recipe with the same run is the same id; another run is another collection
    assert _create(tmp_path, wr.recipe(), name="w", run=RUN).collection_id == c.collection_id
    assert template.from_recipe("wikidata", wr.recipe(), platform_image=PLATFORM,
                                calibration=(c.space.calibration.vectors, c.space.envelope,
                                             c.space.calibration.query_vectors),
                                run="20261101").collection_id != c.collection_id


def test_a_recipe_naming_its_run_keeps_its_files_and_refuses_another(tmp_path):
    r = wr.recipe()
    r["origin"]["spec"] = {"files": [{"rel_path": FILES[0], "size": 103_409_622_204},
                                     {"rel_path": FILES[1]}, {"rel_path": FILES[2]}]}
    c = _create(tmp_path, r, name="w")
    assert origin_run.codex_run(c) == RUN and c.origin.spec["files"][0]["size"] == 103_409_622_204, \
        "the recipe's own files, sizes kept"
    assert c.origin.spec["delta"]["mode"] == "dated-dumps"
    with pytest.raises(template.TemplateError, match="name run 20261012"):
        _create(tmp_path, r, name="w2", run="20261101")
    r["origin"]["spec"] = {"files": [{"rel_path": FILES[0]}]}
    with pytest.raises(template.TemplateError, match="pins 1 origin files that no dated snapshot"):
        _create(tmp_path, r, name="w3")
    r["origin"]["spec"] = {"files": None, "delta": {"mode": "full-resync", "subdir": None}}
    with pytest.raises(template.TemplateError, match="publishes dated dumps"):
        _create(tmp_path, r, name="w3", run=RUN)


def test_a_run_for_an_origin_of_no_dated_dumps_is_refused(tmp_path):
    from tests.hold.test_template import recipe
    with pytest.raises(template.TemplateError, match="publishes no dated dumps"):
        _create(tmp_path, recipe(), name="g", run=RUN, plugin="gutenberg")


def test_wikipedias_codex_is_unchanged(tmp_path):
    """A Codex already naming its run (wikipedia's) is written as before,
    byte for byte: the pin adds nothing to it."""
    from tests.hold import wiki_runs
    c = wiki_runs.codex(tmp_path, "wikipedia")
    spec = c.origin.spec
    assert spec["files"] == [{"rel_path": f.rel_path, "size": None, "checksum": None}
                             for f in __import__("app.acquire.specs", fromlist=["x"]).ACQUIRE_REGISTRY[
                                 "wikipedia"].snapshot_files(wiki_runs.FIRST)
                             if __import__("app.acquire.spec", fromlist=["x"]).admitted(f.rel_path, spec["filters"])]
    assert set(spec) == {"source_id", "transport", "url", "layout", "listing", "files", "filters", "checksums",
                         "delta"}
    # byte for byte: pinned or not, the recipe that states its whole half is the same Codex
    from unittest import mock

    from app.acquire.specs import ACQUIRE_REGISTRY
    with mock.patch.object(template, "pin_dated_spec", lambda plugin, s, run: s):
        unpinned = wiki_runs.codex(tmp_path / "unpinned", "wikipedia")
    assert unpinned.canonical() == c.canonical()
    assert origin_run.pinned_spec(spec, ACQUIRE_REGISTRY["wikipedia"], wiki_runs.FIRST) == spec, \
        "nothing the recipe states is touched"
    # and the half it states is the one the plugin's spec gives a recipe that states none
    half = origin_run.identifying_half(ACQUIRE_REGISTRY["wikipedia"])
    assert {k: spec[k] for k in half} == half


# ---- the guards ----------------------------------------------------------------

def test_nothing_creates_a_keyed_codex_that_names_no_run(tmp_path):
    """A whole Codex at parser 3 with `{"files": null}`, as the runbook's
    step 2 made it on 2026-11-17: refused, in plain words, by every create."""
    from app.hold import codex as cx
    _create(tmp_path, wr.recipe(), name="wikidata", run=RUN)
    d = json.loads((tmp_path / "wikidata" / "shard" / "codex.json").read_bytes())
    d["parser"]["version"], d["origin"]["spec"] = 3, {"files": None}
    d.pop("collection_id", None)
    bad = cx.from_dict(d)
    with pytest.raises(CodexError, match="names each record's origin by the entity id") as e:
        col.create(tmp_path, bad, name="wikidata.bad")
    assert "no `origin.spec.delta.mode`" in str(e.value) and "--run" in str(e.value)
    assert not (tmp_path / "wikidata.bad").exists()
    # at parser 2 the record names its run itself: not refused by this guard
    d["parser"]["version"] = 2
    assert origin_run.why_no_run(cx.from_dict(d)) is None


def test_the_whole_codex_path_pins_the_run_its_predecessor_holds(data_dir, config_dir, isolated_config,
                                                                 tmp_path, capsys):
    """`create --recipe codex.json --name wikidata.next`: the run wikidata's
    records were read in, from its file's `snapshot`, said; `--run` given
    agrees or is refused; a name re-creating nothing needs `--run`."""
    root, _corpus, live = wr.live(data_dir, config_dir)
    assert origin_run.named_run(live) is None and origin_run.held_run(root, "wikidata", live) == RUN
    p = wr.whole_recipe(root, tmp_path)
    assert louise.main(wr.create_args(root, p, "other")) == 2, "no run anywhere, nothing re-created"
    err = capsys.readouterr().err
    assert "names the FIRST run" in err and "lists no complete run" in err
    assert not (root / "other").exists()
    assert louise.main(wr.create_args(root, p, "wikidata.next", "--run", "20261101")) == 0
    out, _err = capsys.readouterr()
    assert origin_run.codex_run(col.read(root, out.strip().splitlines()[-1])) == "20261101"
    assert louise.main(wr.create_args(root, p, "wikidata.next2")) == 2
    capsys.readouterr()
    assert louise.main(wr.create_args(root, p, "wikidata.next")) == 2, "the name holds another already"
    capsys.readouterr()


def test_a_recreation_takes_the_run_its_predecessor_holds_and_says_so(data_dir, config_dir, isolated_config,
                                                                     tmp_path, capsys):
    root, _corpus, _live = wr.live(data_dir, config_dir)
    p = wr.whole_recipe(root, tmp_path)
    assert louise.main(wr.create_args(root, p)) == 0
    out, err = capsys.readouterr()
    assert f"run: {RUN} — the run wikidata's records were read in (collections/wikidata.toml `snapshot`)" in err
    new = col.read(root, out.strip().splitlines()[-1])
    assert origin_run.codex_run(new) == RUN and new.parser.version == 3


def test_nothing_is_carried_into_or_frozen_for_a_keyed_codex_naming_no_run(data_dir, config_dir, isolated_config,
                                                                           tmp_path, monkeypatch, capsys):
    """The node's state of 2026-11-17, made by the code before the guards:
    `dev carry`, `records carry`, `records build` and `snapshot create` each
    refuse it before writing anything, naming why."""
    root, corpus, _live = wr.live(data_dir, config_dir)
    p = wr.whole_recipe(root, tmp_path)
    with monkeypatch.context() as m:
        m.setattr(origin_run, "refuse_without_run", lambda *a, **k: None)
        m.setattr(louise, "_whole_codex_run", lambda root, spec, run, name: spec)
        assert louise.main(wr.create_args(root, p)) == 0
    capsys.readouterr()
    bad = col.read(root, "wikidata.next")
    assert origin_run.why_no_run(bad)
    with pytest.raises(CodexError, match="carry into wikidata.next"):
        carry_mod.carry(root, "wikidata", "wikidata.next", rebuilt=True, apply=True, log=lambda *_: None)
    assert (root / "wikidata" / "state" / "manifest.sqlite").is_file(), "nothing moved"
    with pytest.raises(line_carry.CarryRefused, match="carry into wikidata.next"):
        line_carry.carry(root, "wikidata.next", "wikidata", log=lambda *_: None)
    assert not (root / "wikidata.next" / "state" / "build").exists()
    with pytest.raises(CodexError, match="records build"):
        rec.build(root, "wikidata.next", corpus_dir=corpus, snapshot_id=wr.SID, log=lambda *_: None)
    with pytest.raises(CodexError, match="snapshot create"):
        sn.create(root, "wikidata.next", snapshot_id=wr.SID, log=lambda *_: None)
    assert not (root / "wikidata.next" / "shard" / "snapshots").exists()


# ---- the re-creation by the book, corrected --------------------------------------

def test_the_recreation_by_the_book_names_its_run(data_dir, config_dir, isolated_config, tmp_path, capsys):
    """Steps 2-6 of the runbook with the corrected Codex: the carries admit
    the run pinned that the source's records were read in, each part is held
    to it, and the snapshot names it — records-vs-origin then runs."""
    root, corpus, _live = wr.live(data_dir, config_dir)
    assert louise.main(wr.create_args(root, wr.whole_recipe(root, tmp_path))) == 0
    capsys.readouterr()
    new = col.read(root, "wikidata.next")
    carry_mod.carry(root, "wikidata", "wikidata.next", rebuilt=True, apply=True, log=lambda *_: None)
    got = line_carry.carry(root, "wikidata.next", "wikidata", log=lambda *_: None)
    assert got.snapshot["origin_run"] == RUN and got.rewritten == got.records > 0
    snap = sn.load(root / "wikidata.next" / "shard" / "snapshots" / f"{wr.SID}.json")
    assert snap.origin_run == RUN and origin_run.run_of(new, snap) == RUN
    assert carry_mod.reconcile(root, "wikidata", "wikidata.next", log=lambda *_: None)["by"] == "line-carry"
    from app.hold import check as chk
    o = chk.run(root, "wikidata.next", new, snap, seed=b"\0" * 32, checks=["records-vs-origin"], sample="1",
                reparse=louise._reparse_from(new, "wikidata.next", corpus, run=RUN),
                origin_unit=louise._origin_unit_from(new, "wikidata.next", corpus, run=RUN)
                ).outcome("records-vs-origin")
    assert o.result == chk.PASS, o.why
    assert o.detail["failed"] == [] and o.detail["compared"] > 0


def test_a_pin_to_another_run_than_the_sources_is_refused(data_dir, config_dir, isolated_config, tmp_path, capsys):
    root, _corpus, _live = wr.live(data_dir, config_dir)
    assert louise.main(wr.create_args(root, wr.whole_recipe(root, tmp_path), "wikidata.next",
                                      "--run", "20261101")) == 0
    capsys.readouterr()
    with pytest.raises(CodexError, match="pins run 20261101, and wikidata's records were read in run 20261012"):
        carry_mod.carry(root, "wikidata", "wikidata.next", rebuilt=True, apply=True, log=lambda *_: None)
    with pytest.raises(line_carry.CarryRefused, match="pins run 20261101"):
        line_carry.carry(root, "wikidata.next", "wikidata", log=lambda *_: None)


def test_a_part_whose_lines_name_another_run_than_the_codex_is_refused(data_dir, config_dir, isolated_config,
                                                                       tmp_path, capsys):
    """The per-part hold (line_carry): the source's file says one run and its
    lines name another — the Codex is pinned to the file's word, and the
    first part read in another run refuses the carry, nothing replaced."""
    root, _corpus, _live = wr.live(data_dir, config_dir)
    toml = config_dir / "collections" / "wikidata.toml"
    toml.write_text(toml.read_text().replace(f'snapshot = "{RUN}"', 'snapshot = "20261101"'))
    assert louise.main(wr.create_args(root, wr.whole_recipe(root, tmp_path))) == 0
    capsys.readouterr()
    assert origin_run.codex_run(col.read(root, "wikidata.next")) == "20261101"
    with pytest.raises(line_carry.CarryRefused, match=f"its lines were read in run {RUN}, and wikidata.next's "
                                                      f"Codex names run 20261101"):
        line_carry.carry(root, "wikidata.next", "wikidata", log=lambda *_: None)
    parts = root / "wikidata.next" / "shard" / "records"
    assert not any(parts.rglob("*.jsonl.zst")) if parts.is_dir() else True, "nothing replaced"
    assert not (root / "wikidata.next" / "shard" / "snapshots").exists()
