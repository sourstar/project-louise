"""tools/measure/eurlex_problem_slice.py: the EUR-Lex5 restart's problem
slice and the rest, from the manifest and the units check (read only)."""
from __future__ import annotations


from app import collections as collections_mod, db
from app.hold import identity
from tools.measure import eurlex_problem_slice as ps


def _coll(tmp_path):
    c = collections_mod.Collection(
        name="EUR-Lex5", plugin="eurlex", profile=None, embed_mode_default=None,
        corpus_dir=tmp_path / "c", manifest_path=tmp_path / "m.db", fts_dir=tmp_path / "f",
        lance_dir=tmp_path / "l")
    con = db.connect(c)
    rows = [  # uid, path, error, fts_done, chunks, size
        ("e:ok", "/data/x/lang=en/2016/32016R0679", None, 1, 40, 100),
        ("e:chunkless", "/data/x/lang=en/2016/32016R0001", None, 1, 0, 100),
        ("e:corr", "/data/x/lang=cs/2019/32019L1922R(01)", None, 1, 3, 100),
        ("e:err", "/data/x/lang=hr/1959/31959R0005", "rdf: UnindexableDocument: …", 0, None, 100),
        ("e:tomb", "/data/x/lang=en/2024/32024R1689", "superseded by e:keep: …", 0, None, 100),
        ("e:keep", "/data/x/lang=en/2024/32024R1689", None, 1, 900, 5_000_000),
        ("e:old", "/data/x/lang=en/2024/32024R1689", None, 1, 800, 4_000_000),
        ("e:pkg", "/data/x/lang=it/2021/32021R1971", None, 1, 70, 200),
        ("e:issue", "/data/x/lang=bg/2004/32004D0382", None, 1, 5, 100),
    ]
    for uid, path, error, fts, chunks, size in rows:
        con.execute("INSERT INTO documents(uid, source, native_id, path, error, fts_done, chunks, size, rdf_done, "
                    "embed_done) VALUES(?,?,?,?,?,?,?,?,1,0)", (uid, "eurlex", str(abs(hash(uid)) % 10**9), path,
                                                                 error, fts, chunks, size))
    con.execute("INSERT INTO issues(uid, code, detail, first_seen, last_seen) VALUES('e:issue', "
                "'formex-not-this-act', '', 0, 0)")
    con.execute("INSERT INTO issues(uid, code, detail, first_seen, last_seen) VALUES('e:ok', "
                "'entry-into-force-before-publication', '', 0, 0)")
    con.commit()
    return c, con


def test_the_slice_names_every_problem_once_and_the_rest_is_the_rest(tmp_path, monkeypatch):
    c, con = _coll(tmp_path)
    monkeypatch.setattr(identity, "plan", lambda con, plugin, log=print: ({}, [("e:old", "e:keep")]))
    flagged = {"/corpus/lang=it/2021/32021R1971": ["package", "included document"]}
    reasons, live, retired = ps.slice_of(con, None, flagged=flagged, largest=1)
    assert retired == {"e:old"} and "e:tomb" not in live and "e:old" not in live
    assert reasons["duplicate-kept"] == {"e:keep"} and reasons["chunkless"] == {"e:chunkless"}
    assert reasons["corrigendum"] == {"e:corr"} and reasons["errored"] == {"e:err"}
    assert reasons["issue:formex-not-this-act"] == {"e:issue"}
    assert reasons["unit:package"] == {"e:pkg"} == reasons["unit:included document"], "matched by slice folder"
    assert reasons["largest"] == {"e:keep"}
    assert not any("e:ok" in v for v in reasons.values()), "a dating issue is no text problem"


def test_the_command_writes_both_lists(tmp_path, monkeypatch, capsys):
    c, con = _coll(tmp_path)
    con.close()
    monkeypatch.setattr(identity, "plan", lambda con, plugin, log=print: ({}, [("e:old", "e:keep")]))
    monkeypatch.setattr(collections_mod, "by_name", lambda name: c if name == "EUR-Lex5" else None)
    units = tmp_path / "flagged.tsv"
    units.write_text("/corpus/lang=it/2021/32021R1971\tpackage, included document\n", encoding="utf-8")
    out = tmp_path / "out"
    assert ps.main(["EUR-Lex5", "--units", str(units), "--largest", "1", "--out-dir", str(out)]) == 0
    problem = set((out / "problem.txt").read_text().split())
    rest = set((out / "rest.txt").read_text().split())
    assert problem == {"e:keep", "e:chunkless", "e:corr", "e:err", "e:issue", "e:pkg"}
    assert rest == {"e:ok"} and not problem & rest
    assert '"retired_by_retire_superseded": 1' in capsys.readouterr().out
    assert ps.main(["nope", "--out-dir", str(out)]) == 2
