"""A statement made before its run's CPU reference was stored is not relied
on: the run is gated against the reference, once for the space (München,
2026-11-12: pubmed's two new references entered a statement only through
`hold embedder check --remeasure`; without it the space's statement, older
than they, was taken and the check printed "no stored CPU reference").
Invariant 12: every card run reaches the stored CPU reference."""
import json
import shutil

from app import collections as collections_mod, db, louise, run_check
from app.hold import cpu_reference as cpu_ref, embedder as emb, envelope as env, runs as hold_runs
from app.verify import backend_gate
from tests.hold.conftest import KEY
from tests.test_check_takes_the_space_statement import _second_in_the_space
from tests.test_run_check import R1, endpoint, node  # noqa: F401


def _without_cpu_comparison(root, name, run_id, role="document"):
    """The statement as it was signed before a reference existed: the same
    run and numbers, `cpu_reference` null, signed by this node's key."""
    p = hold_runs.path_for(root, name, run_id, role)
    body = emb.read_statement(json.loads(p.read_bytes()))
    assert body["cpu_reference"] is not None
    p.write_text(json.dumps(env.sign(env.CALIBRATION, {**body, "cpu_reference": None}, KEY)),
                 encoding="utf-8")
    hold_runs._read.clear()
    run_check.reset_cache()
    backend_gate.reset_cache()


def _measured(monkeypatch):
    calls = []
    real = run_check.validate
    monkeypatch.setattr(run_check, "validate",
                        lambda *a, **k: calls.append((a[1], k.get("role", "document"))) or real(*a, **k))
    return calls


def test_the_worker_gates_a_run_whose_statement_predates_its_reference(node, monkeypatch):
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    _without_cpu_comparison(root, "gutenberg", R1)
    calls = _measured(monkeypatch)
    assert backend_gate.embed_allowed(con, collection) is True
    assert ("gutenberg", "document") in calls, "gated again, against the reference"
    body = hold_runs.statements_for_space(root, "gutenberg", codex, "document", signer=run_check.signer())[R1]
    assert body["cpu_reference"] is not None and hold_runs.passing(body, codex)
    calls.clear()
    run_check.reset_cache()
    backend_gate.reset_cache()
    assert backend_gate.embed_allowed(con, collection) is True and calls == [], "once"


def test_with_no_reference_stored_the_statement_is_taken_as_before(node, monkeypatch):
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    _without_cpu_comparison(root, "gutenberg", R1)
    for role in ("document", "query"):
        shutil.rmtree(cpu_ref.directory(root, "gutenberg", role))
    calls = _measured(monkeypatch)
    assert backend_gate.embed_allowed(con, collection) is True and calls == []


def test_the_spaces_statement_held_to_the_reference_replaces_one_that_was_not(node, tmp_path):
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True        # gutenberg's, with the comparison
    _second_in_the_space(root, tmp_path, codex)
    who = run_check.signer()
    assert hold_runs.statements_for_space(root, "neuris", codex, "document", signer=who)[R1]
    _without_cpu_comparison(root, "neuris", R1)                       # neuris's own copy predates it
    got = hold_runs.statements_for_space(root, "neuris", codex, "document", signer=who)[R1]
    assert got["cpu_reference"] is not None, "the one held to the reference wins"
    own = emb.read_statement(json.loads(hold_runs.path_for(root, "neuris", R1, "document").read_bytes()))
    assert own["cpu_reference"] is not None, "copied over the weaker one: one borrowed is one kept"


def test_the_command_gates_it_instead_of_saying_no_reference(node, capsys, monkeypatch):
    root, collection, codex = node
    con = db.connect(collection)
    assert backend_gate.embed_allowed(con, collection) is True
    _without_cpu_comparison(root, "gutenberg", R1)
    calls = _measured(monkeypatch)
    assert louise.main(["hold", "embedder", "check", "gutenberg", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert ("gutenberg", "document") in calls and "no stored CPU reference" not in out
