"""THE CARD CAP (operator, 2026-11-27: "For test purpose we can limit the b70
to 16 GB to pre optimize the scenario"; app/card_cap.py,
docs/notes/perugia-16gb-test.md §0).

`[models] card_cap_mib`, written by `node resident set --card-cap MIB` into
platform.toml, is applied in ONE place — `gpu.from_json`, where Louise reads
the probe — and every consumer of the card's memory sees total = min(its
own, the cap) with the driver's used unchanged: past the cap, no room, and
one node event per crossing."""
from __future__ import annotations

import json
import sqlite3

import pytest

from app import (api_commands, card_cap, config, gate_twin, gpu, gpu_models, louise, model_evict, model_load,
                 platform_config, query_trace, question_card, resident)
from tests.test_gpu_panel import client  # noqa: F401 — the System page's fixture
from tests.test_resident import SERVER, _beat, _system, node  # noqa: F401 — the keeper's fixture

MIB = 1 << 20
REAL, CAP = 32_656, 16_384


def _probe(monkeypatch, used_mib: int, *, igpu: bool = False) -> dict:
    """The GPU probe's GET /gpu: the B70 (and the processor's Radeon)."""
    state = {"used": used_mib}
    monkeypatch.setattr(config, "GPU_PROBE_URL", "http://gpu-probe:8080")

    def answer(url):
        assert url == "http://gpu-probe:8080/gpu"
        devices = [{"card": "card1", "pdev": "0000:03:00.0", "driver": "xe", "vendor_id": "0x8086",
                    "device_id": "0xe223", "vendor": "Intel", "name": "Arc Pro B70",
                    "vram_total": REAL * MIB, "vram_used": state["used"] * MIB, "integrated": False,
                    "processes": [{"pid": 7, "label": "louise-chat", "command": "llama-server",
                                   "container": "localai", "vram": state["used"] * MIB, "host": 0}]}]
        if igpu:
            devices.append({"card": "card2", "pdev": "0000:c6:00.0", "driver": "amdgpu", "vendor_id": "0x1002",
                            "device_id": "0x164e", "vendor": "AMD", "name": "Radeon 610M",
                            "vram_total": 20_000 * MIB, "vram_used": 100 * MIB, "integrated": True})
        return {"devices": devices}
    monkeypatch.setattr(gpu, "_get_json", answer)
    return state


def _set(value, capsys) -> dict:
    assert louise.main(["node", "resident", "set", "--card-cap", str(value)]) == 0
    return json.loads(capsys.readouterr().out)


@pytest.fixture
def capped(isolated_config, config_dir, monkeypatch, capsys):
    """The B70 at 18,300 MiB used, capped at 16,384 by the command; the
    reclaimer's line 0.95."""
    state = _probe(monkeypatch, 18_300)
    _set(CAP, capsys)
    monkeypatch.setattr(resident, "_threshold", lambda server: 0.95)
    resident.reset()
    model_load.reset()
    yield state
    resident.reset()
    model_load.reset()


def _uncap(capsys) -> None:
    _set("", capsys)


# ---- the one place ----------------------------------------------------------------

def test_the_cap_is_applied_where_the_probe_is_read_and_nowhere_else(monkeypatch):
    _probe(monkeypatch, 10_000, igpu=True)
    data = gpu._get_json("http://gpu-probe:8080/gpu")
    b70, igpu = gpu.from_json(data, cap=CAP * MIB).devices
    assert b70.vram_total == CAP * MIB and b70.vram_total_real == REAL * MIB, "the card's own kept beside it"
    assert b70.vram_used == b70.used == 10_000 * MIB, "used as the driver counts it"
    assert b70.free == (CAP - 10_000) * MIB and b70.capped and b70.cap_says == "capped at 16,384 MiB of 32,656"
    assert igpu.vram_total == 20_000 * MIB and igpu.vram_total_real is None, "the processor's memory is the system's"
    # uncapped, or a cap above the card: the card as it is
    for cap in (None, 40_000 * MIB):
        d = gpu.from_json(data, cap=cap).devices[0]
        assert d.vram_total == REAL * MIB and d.vram_total_real is None and not d.capped and d.cap_says == ""
    # idempotent: a capped snapshot read again is capped from the card's own total
    again = gpu.from_json(gpu.to_json(gpu.from_json(data, cap=CAP * MIB)), cap=CAP * MIB).devices[0]
    assert (again.vram_total, again.vram_total_real) == (CAP * MIB, REAL * MIB)
    back = gpu.from_json(gpu.to_json(gpu.from_json(data, cap=CAP * MIB)), cap=None).devices[0]
    assert (back.vram_total, back.vram_total_real) == (REAL * MIB, None)


def test_the_default_is_the_file_s_cap(isolated_config, monkeypatch, capsys):
    _probe(monkeypatch, 10_000)
    data = gpu._get_json("http://gpu-probe:8080/gpu")
    assert gpu.from_json(data).devices[0].vram_total == REAL * MIB, "unset by default"
    _set(CAP, capsys)
    assert card_cap.cap_mib() == CAP and gpu.from_json(data).devices[0].vram_total == CAP * MIB
    _set(20_000, capsys)                       # the same size of file, at once: read again all the same
    assert gpu.from_json(data).devices[0].vram_total == 20_000 * MIB
    _uncap(capsys)
    assert card_cap.cap_mib() is None and gpu.from_json(data).devices[0].vram_total == REAL * MIB


def test_over_the_cap_the_card_reads_no_room():
    d = gpu.from_json({"devices": [{"card": "c", "pdev": "p", "driver": "xe", "vendor_id": "v", "device_id": "d",
                                    "vendor": None, "name": "B70", "vram_total": REAL * MIB,
                                    "vram_used": 18_300 * MIB}]}, cap=CAP * MIB).devices[0]
    assert d.free == 0 and d.over_cap == (18_300 - CAP) * MIB and d.used == 18_300 * MIB
    assert d.used_pct == 100.0


# ---- every consumer sees it --------------------------------------------------------

def test_the_keeper_s_card_is_the_capped_one(capped):
    assert resident._card() == (CAP * MIB, 18_300 * MIB)
    assert resident._physical((CAP * MIB, 18_300 * MIB)) == (REAL * MIB, 18_300 * MIB), \
        "the card's own total, for what the driver does"


def test_the_keeper_s_room_check_sees_the_cap(capped):
    with model_load._lock:
        model_load._sizes[(SERVER, "vega-4b")] = 1_000 * MIB
    r = resident.Resident(SERVER, "vega-4b", ("query encoder",), ("gutenberg",))
    got = resident.room(r)
    assert got.free == int(0.95 * CAP * MIB) - 18_300 * MIB and not got.fits
    assert got.says().startswith("needs 1,000 MiB, 0 MiB free under the 0.95 line")
    capped["used"] = 10_000
    assert resident.room(r).fits


def test_a_load_s_eviction_sees_the_cap(capped, monkeypatch):
    monkeypatch.setattr(model_load, "held_sizes", lambda *a, **k: None)
    got = model_evict.make_room(SERVER, need=1_000 * MIB, horizon=None, for_what="a test")
    assert got.fits is None and got.free == int(0.95 * CAP * MIB) - 18_300 * MIB, \
        "past the cap: room to make, none free"
    capped["used"] = 10_000
    assert model_evict.make_room(SERVER, need=1_000 * MIB, horizon=None, for_what="a test").fits is True


def test_a_question_s_room_sees_the_cap(capped):
    assert question_card._free(SERVER) == int(0.95 * CAP * MIB) - 18_300 * MIB


def test_a_gate_s_twin_sees_the_cap(capped, monkeypatch):
    monkeypatch.setattr(model_load, "activity", lambda server: {})
    with model_load._lock:
        model_load._sizes[(SERVER, "vega-4b")] = 1_000 * MIB
    with pytest.raises(gate_twin.RoomShort, match=r"needs ~1,512 MiB free on the card and 0 MiB are"):
        gate_twin.make_room(SERVER, "vega-4b", wait_s=0)
    capped["used"] = 10_000
    assert gate_twin.make_room(SERVER, "vega-4b", wait_s=0) == (1_000 * MIB, [])


def test_the_system_page_says_the_cap(client, monkeypatch, capsys):  # noqa: F811
    _probe(monkeypatch, 18_300)
    _set(CAP, capsys)
    html = client.get("/admin/system/gpu").text
    assert "VRAM 16.00 GiB · used 17.87 GiB" in html and "free 0 B" in html
    assert "capped at 16,384 MiB of 32,656" in html and "over the cap by 1.87 GiB" in html


def test_the_models_bar_is_of_the_capped_card(isolated_config, monkeypatch, capsys):
    _probe(monkeypatch, 18_300)
    _set(CAP, capsys)
    devices = gpu.panel(config.GPU_PROBE_URL)["devices"]
    models = {"rows": [gpu_models.ModelRow("louise-chat", "llama-cpp", 9_000 * MIB, ("chat",))],
              "asked": ["http://localai:8080"], "failed": [], "louise": 9_000 * MIB, "other": 0}
    cards = gpu_models.by_card(models, devices)
    assert [r.model for r in cards["0000:03:00.0"]["rows"]] == ["louise-chat"]
    d = devices[0]
    bar = gpu_models.bar(models, d.vram_total, d.used)
    assert bar["louise"] == pytest.approx(100.0 * 9_000 / CAP) and bar["free"] == 0.0


def test_a_query_trace_records_both_totals(isolated_config, monkeypatch, capsys):
    _probe(monkeypatch, 18_300)
    _set(CAP, capsys)
    card = query_trace._gpus()[0]
    assert card["vram_total"] == CAP * MIB and card["vram_total_real"] == REAL * MIB
    _uncap(capsys)
    assert "vram_total_real" not in query_trace._gpus()[0]


# ---- over the cap: the keeper's finding -------------------------------------------

def test_over_the_cap_no_room_and_one_event_per_crossing(tmp_path, node, monkeypatch, capsys):  # noqa: F811
    node.held |= {"louise-chat", "louise-translate"}
    _system(monkeypatch, node, {"louise-chat": 9_000, "louise-translate": 9_000})
    card = _probe(monkeypatch, 18_300)
    _set(CAP, capsys)
    _beat([])

    def warns():
        return [t for level, t in node.said if level == "warn" and "OVER ITS CAP" in t]
    assert len(warns()) == 1
    w = warns()[0]
    assert "the driver counts 18,300 MiB used, 1,916 MiB over the cap of 16,384 MiB" in w
    assert "a load Louise did not plan" in w
    assert "loaded: louise-chat 9,000 MiB, louise-translate 9,000 MiB" in w
    assert any("capped at 16,384 MiB of 32,656" in t for _, t in node.said), "the cap said once"
    # the card ITSELF holds them whole (18,000 MiB at rest of 32,656): the
    # driver pages nothing, and a cap says nothing of it
    assert not any("OVER-COMMITTED" in t for _, t in node.said)
    # the budget is the capped card's
    assert resident.read_memory().total == CAP * MIB
    _beat([])
    assert len(warns()) == 1, "once per crossing"
    card["used"] = 15_000
    _beat([])
    assert any("back under its cap of 16,384 MiB" in t for _, t in node.said)
    card["used"] = 17_000
    _beat([])
    assert len(warns()) == 2, "the next crossing is said again"
    _uncap(capsys)
    _beat([])
    assert any("no longer capped" in t for _, t in node.said)
    assert resident.read_memory().total == REAL * MIB


def test_node_resident_show_says_the_cap(tmp_path, node, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr(resident, "served_now", lambda: ([], {}))
    _probe(monkeypatch, 18_300)
    _set(CAP, capsys)
    out = resident.show()                     # before any beat: the card as the next one reads it
    assert out["card"]["says"].startswith("capped at 16,384 MiB of 32,656 (platform.toml [models] card_cap_mib")
    assert "OVER THE CAP by 1,916 MiB" in out["card"]["says"]
    assert (out["card"]["total_mib"], out["card"]["real_total_mib"], out["card"]["used_mib"],
            out["card"]["free_mib"], out["card"]["card_cap_mib"]) == (CAP, REAL, 18_300, 0, CAP)
    assert out["budget"]["line_mib"] == int(0.95 * CAP * MIB) // MIB
    _uncap(capsys)
    out = resident.show()
    assert out["card"]["says"] == "32,656 MiB, its own" and "real_total_mib" not in out["card"]


# ---- the command ------------------------------------------------------------------

FILE = '# the node\n[models]\nchat_large = "louise-chat-large"   # query mode\n\n[node]\ntitle = "Casa"\n'


def test_the_command_writes_and_removes_it_keeping_every_other_line(isolated_config, config_dir, monkeypatch,
                                                                    capsys):
    _probe(monkeypatch, 10_000)
    path = config_dir / "platform.toml"
    path.write_text(FILE)
    out = _set(CAP, capsys)
    assert out["card_cap_mib"] == CAP and out["changed"] == ["card_cap_mib"] and out["card_total_mib"] == REAL
    assert "the model server's own reclaimer and the engine still see the whole card" in out["says"].lower()
    assert path.read_text() == FILE.replace('query mode\n', 'query mode\ncard_cap_mib = 16384\n')
    assert platform_config.load().models.card_cap_mib == CAP
    _uncap(capsys)
    assert path.read_text() == FILE and platform_config.load().models.card_cap_mib is None


@pytest.mark.parametrize("value, says", [
    ("1000", "at least 4,096 MiB, not 1,000"),
    ("16 GB", "a whole number of MiB"),
    ("40000", "above the card's own 32,656 MiB (Arc Pro B70): a cap above the card would cap nothing"),
])
def test_the_command_refuses_a_bad_value_in_words(isolated_config, config_dir, monkeypatch, capsys, value, says):
    _probe(monkeypatch, 10_000)
    path = config_dir / "platform.toml"
    path.write_text(FILE)
    assert louise.main(["node", "resident", "set", "--card-cap", value]) == 2
    assert says in capsys.readouterr().err
    assert path.read_text() == FILE, "nothing changed"


def test_the_command_with_nothing_to_set_is_exit_2(isolated_config, capsys):
    assert louise.main(["node", "resident", "set"]) == 2
    assert "give --card-cap MIB" in capsys.readouterr().err


def test_with_no_probe_it_is_written_and_said_unchecked(isolated_config, config_dir, monkeypatch, capsys):
    monkeypatch.setattr(config, "GPU_PROBE_URL", "")
    out = _set(CAP, capsys)
    assert out["card_cap_mib"] == CAP and "not checked against it" in out["note"]


@pytest.mark.parametrize("text", ['"16384"', "1000", "true", "16384.0"])
def test_a_bad_value_in_the_file_is_refused_at_load(config_dir, monkeypatch, text):
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    (config_dir / "platform.toml").write_text(f"[models]\ncard_cap_mib = {text}\n")
    with pytest.raises(platform_config.schedule_mod.ScheduleError, match="models.card_cap_mib"):
        platform_config.load()


def test_the_route_follows_from_the_command_tree():
    path, parser = api_commands.resolve(["node", "resident", "set"])
    assert path == ("node", "resident", "set") and "set" not in api_commands.READ_VERBS, "a POST, run by the worker"
    argv = api_commands.argv_for(path, parser, {"card_cap": 16384})
    assert argv[:5] == ["node", "resident", "set", "--card-cap", "16384"]
    assert api_commands.argv_for(path, parser, {"card_cap": ""})[3:5] == ["--card-cap", ""]


# ---- invariant 10: the file is the desired state ----------------------------------

def test_the_cap_is_the_file_s_and_nothing_of_it_is_in_a_database(isolated_config, config_dir, tmp_path,
                                                                   monkeypatch, capsys):
    from app import node_state
    _probe(monkeypatch, 10_000)
    node_state.set_worker_state("running")            # the node's state database exists
    _set(CAP, capsys)
    assert "card_cap_mib = 16384" in (config_dir / "platform.toml").read_text()
    dbs = {node_state.path(), *tmp_path.rglob("*.sqlite")}
    assert node_state.path().is_file()
    for db in dbs:
        with sqlite3.connect(db) as con:
            assert "card_cap" not in "\n".join(con.iterdump()), f"{db}: desired state in a database"
    # the value shown is the worker's own resolution: the file, read as `from_json` reads it
    assert card_cap.cap_mib() == platform_config.load().models.card_cap_mib == CAP
    _uncap(capsys)
    assert card_cap.cap_mib() is None and resident._card() == (REAL * MIB, 10_000 * MIB)
