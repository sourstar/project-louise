"""`hold embedder check` takes the space's statement for the run answering
now, as the worker does (03 §3.3: a pass validates the run FOR THE SPACE).

NeuRIS's `new`, 2026-11-12: its step 5 ran `hold embedder check`, which went
straight to the full gate — 27 minutes on the shared Nemotron load, three
reloads that cut normattiva-nemo's and legi's embedding, and a deferral at
the end — for a run the space had validated for normattiva-nemo hours
before. The worker would have admitted the collection on that statement
(#574: the batching record comes with it). The command line now does the
same, re-checking the statement's sixteen texts on this load; `--remeasure`
gates it again when that is what is wanted."""
import shutil

from app import collections as collections_mod, louise, run_check
from app.hold import collection as col, cpu_reference as cpu_ref, runs as hold_runs
from tests.hold.conftest import KEY, NOW, PLATFORM
from tests.hold.test_template import recipe as a_recipe
from tests.test_run_check import (DOC_URL, QUERY_URL, endpoint, node,  # noqa: F401
                                  opening, opening_query)


def _second_in_the_space(root, tmp_path, codex):
    """`neuris`: a new collection of gutenberg's space, on the same encoder."""
    made = col.create_from_recipe(root, "gutenberg", a_recipe(work={"tokens": 6_000_000}),
                                  platform_image=PLATFORM, embed=opening,
                                  embed_query=opening_query, name="neuris", key=KEY, now=NOW)
    assert made.space_id == codex.space_id and made.collection_id != codex.collection_id
    (tmp_path / "config" / "collections" / "neuris.toml").write_text(
        f'codex = "{made.collection_id}"\nembed = "on-demand"\n'
        f'[encoder]\nurl = "{DOC_URL}"\nquery_url = "{QUERY_URL}"\n'
        f'embed_model = "medcpt-article"\nquery_model = "medcpt-query"\n', encoding="utf-8")
    for role in ("document", "query"):
        shutil.copytree(cpu_ref.directory(root, "gutenberg", role),
                        cpu_ref.directory(root, "neuris", role))
    return made


def _gated_for_the_space(root, collection, codex):
    """gutenberg's gate passed for the run, both sides, as the worker keeps it."""
    rec = collections_mod.recipe_for(collection)
    for role in ("document",) + (("query",) if codex.space.query.encoder else ()):
        assert hold_runs.passing(run_check.validate(root, "gutenberg", codex, rec, role=role,
                                                    key=KEY), codex)


def test_check_takes_the_spaces_statement_and_re_checks_it(node, endpoint, tmp_path, capsys,
                                                         monkeypatch):
    root, collection, codex = node
    _gated_for_the_space(root, collection, codex)
    made = _second_in_the_space(root, tmp_path, codex)
    run_check.reset_cache()
    measured = []
    real = run_check.validate
    monkeypatch.setattr(run_check, "validate",
                        lambda *a, **k: measured.append(k.get("role")) or real(*a, **k))
    before = endpoint.texts
    assert louise.main(["hold", "embedder", "check", "neuris", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    roles = 2 if made.space.query.encoder is not None else 1
    assert measured == [], "no gate measured"
    assert out.count("PASS") == roles and out.count("taken       the space's statement") == roles
    assert endpoint.texts - before <= roles * (1 + 16), "the probe and the sixteen texts, no more"
    for role in ("document",) + (("query",) if roles == 2 else ()):
        assert hold_runs.path_for(root, "neuris", run_check.answering_run(
            collections_mod.recipe_for(next(c for c in collections_mod.load_all()
                                            if c.name == "neuris")),
            query=role == "query", probe_item=run_check.probe_for(made, role)),
            role).exists(), "one borrowed is one kept"


def test_remeasure_gates_the_run_again(node, endpoint, tmp_path, capsys, monkeypatch):
    root, collection, codex = node
    _gated_for_the_space(root, collection, codex)
    _second_in_the_space(root, tmp_path, codex)
    run_check.reset_cache()
    measured = []
    real = run_check.validate
    monkeypatch.setattr(run_check, "validate",
                        lambda *a, **k: measured.append(k.get("role")) or real(*a, **k))
    assert louise.main(["hold", "embedder", "check", "neuris", "--root", str(root),
                        "--remeasure"]) == 0
    assert measured and measured[0] == "document"
    assert "taken" not in capsys.readouterr().out


def test_a_space_with_no_statement_for_the_run_is_gated(node, endpoint, tmp_path, capsys,
                                                       monkeypatch):
    """Nothing to take: the gate measures, exactly as before."""
    root, collection, codex = node
    _second_in_the_space(root, tmp_path, codex)
    run_check.reset_cache()
    measured = []
    real = run_check.validate
    monkeypatch.setattr(run_check, "validate",
                        lambda *a, **k: measured.append(k.get("role")) or real(*a, **k))
    assert louise.main(["hold", "embedder", "check", "neuris", "--root", str(root)]) == 0
    assert measured and "taken" not in capsys.readouterr().out
