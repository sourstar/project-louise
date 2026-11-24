"""tools/measure/moe_record_replay.py: what a record of the experts used would save, replayed from the engine's
routing traces (docs/notes/moe-expert-record.md §5).

The tool's cache is llama.cpp's own simulator, copied; its record, fill and seeded eviction are ours and mirror staged
0017. The golden figures below were produced on 2026-11-24 by the ENGINE's own `moe_cache_lru` (extracted from the
candidate's src/llama-moe-cache.cpp) and `llama_moe_record`, driven as the cache drives them, on these same lookups —
40 further random cases agreed with it and with scripts/moe-cache-tune.py's replay() the same day.
"""
from __future__ import annotations

import importlib.util
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "measure" / "moe_record_replay.py"
PATCH = ROOT / "deploy" / "localai" / "patches" / "staged" / "0017-llama-cpp-moe-record.patch"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("moe_record_replay", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_trace(path: Path, n_layer: int, n_expert: int, n_used: int, records: list[tuple[int, list[int]]],
                nb2: tuple[int, ...] = (1 << 20,), tail: bytes = b"") -> Path:
    """one group of all the layers, as src/llama-moe-cache.cpp's write_trace_header and write_trace"""
    host = n_layer * n_expert * sum(nb2)
    b = bytearray(b"MCT1")
    b += struct.pack("<3I", n_layer, n_used, 1)
    b += struct.pack("<3I", 0, n_expert, n_layer) + struct.pack(f"<{n_layer}I", *range(n_layer))
    b += struct.pack("<2d", 1.0, 1.0) + struct.pack("<3Q", host, host, 64)
    b += struct.pack("<I", len(nb2)) + struct.pack(f"<{len(nb2)}Q", *nb2)
    for il, ids in records:
        b += struct.pack("<3i", il, n_used, len(ids)) + struct.pack(f"<{len(ids)}i", *ids)
    path.write_bytes(bytes(b) + tail)
    return path


class Lcg:
    """a generator that is the same everywhere: the golden figures depend on every draw"""

    def __init__(self, seed: int) -> None:
        self.x = seed

    def below(self, n: int) -> int:
        self.x = (self.x * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (self.x >> 33) % n


def answer(rng: Lcg, n_layer: int, n_expert: int, n_used: int, n_tokens: int, shift: int) -> list[tuple[int, list[int]]]:
    """the lookups of an answer: a batch of 5 tokens, then one token at a time; a skewed choice of experts, moved by
    `shift` (another subject uses other experts)"""
    records: list[tuple[int, list[int]]] = []
    done = 0
    while done < n_tokens:
        n = 5 if done == 0 else 1
        per_layer: dict[int, list[int]] = {il: [] for il in range(n_layer)}
        for _ in range(n):
            for il in range(n_layer):
                chosen: list[int] = []
                while len(chosen) < n_used:
                    e = (min(rng.below(n_expert), rng.below(n_expert), rng.below(n_expert)) + shift + 7 * il) % n_expert
                    if e not in chosen:
                        chosen.append(e)
                per_layer[il] += chosen
        records += [(il, per_layer[il]) for il in range(n_layer)]
        done += n
    return records


def golden_loads(tmp_path: Path) -> list[Path]:
    rng = Lcg(20261124)
    shifts = [0, 0, 0, 16]      # three answers on a subject, a fourth on another
    return [write_trace(tmp_path / f"load{i}.trace", 4, 32, 4, answer(rng, 4, 32, 4, 300, shift))
            for i, shift in enumerate(shifts)]


# per load: cold uploads under the first 16, 64, 256 tokens and in all; the same on a cache filled from the record
# of the loads before; the experts the fill uploads; the four again with the flat seed. From the engine's own class
# and record (see the docstring).
GOLDEN = [
    [131, 402, 1442, 1659, 131, 402, 1442, 1659, 0, 131, 402, 1442, 1659],
    [147, 416, 1445, 1685, 96, 364, 1381, 1619, 48, 111, 384, 1410, 1650],
    [137, 373, 1391, 1616, 89, 300, 1303, 1528, 48, 99, 326, 1345, 1569],
    [117, 420, 1468, 1704, 150, 522, 1609, 1846, 48, 110, 424, 1475, 1712],
]


# the fourth load with the seeds capped at a CRF of 4, from the same class
CAP_4_OTHER_SUBJECT = [138, 487, 1553, 1792]


# loads 2 to 4 with HALF the cache filled (LLAMA_MOE_RECORD_FILL=0.5): the uploads under 16, 64, 256 tokens and in
# all, and the experts the fill uploads; from the same class and the engine's own llama_moe_record::fill_slots
HALF_FILL = [[108, 367, 1386, 1625, 24], [99, 321, 1336, 1561, 24], [143, 502, 1584, 1820, 24]]


def uploads(row: dict) -> list[int]:
    return [row["uploads"][n][0] for n in (16, 64, 256, "all")]


def test_the_replay_is_the_engines_cache_and_record_on_a_golden_case(tool, tmp_path) -> None:
    rows = tool.run([str(p) for p in golden_loads(tmp_path)], 49)
    assert rows[0][1]["slots"] == 48 and rows[0][1]["tokens"] == 300
    got = []
    for _, cold, filled, flat, capped, _ in rows:
        if filled is None:
            got.append(uploads(cold) * 2 + [0] + uploads(cold))
        else:
            assert flat["fill"] == filled["fill"] and capped == {1.0: flat}
            got.append(uploads(cold) + uploads(filled) + [filled["fill"][0]] + uploads(flat))
    assert got == GOLDEN
    # the subject the record knows: the fill removes uploads under the first tokens
    assert got[1][4] < got[1][0] and got[2][5] < got[2][1]
    # another subject, a cache a third the size of the experts: the wrong guesses, seeded with their record's rank,
    # hold their slots and cost uploads to the end of the answer; seeded flat they are the first to go
    assert got[3][7] > got[3][3] and got[3][12] <= got[3][7]


def test_a_cold_cache_with_room_uploads_each_expert_once(tool, tmp_path) -> None:
    # 2 layers, 8 experts, room for all 16: the uploads are the distinct experts, and the same answer again on a cache
    # filled from the record uploads nothing under its tokens - the fill made them, inside the load
    records = [(0, [0, 1]), (1, [2, 3]), (0, [1, 4]), (1, [3, 3 + 2]), (0, [0, 1]), (1, [2, 5])]
    first = write_trace(tmp_path / "a.trace", 2, 8, 2, records)
    again = write_trace(tmp_path / "b.trace", 2, 8, 2, records)
    (_, cold, none, _, _, _), (_, cold_again, filled, flat, _, _) = tool.run([str(first), str(again)], 64)
    assert none is None
    assert cold["tokens"] == 3 and cold["lookups"] == 6 and cold["large"] == 0
    assert cold["uploads"]["all"] == (6, 6 << 20)             # experts 0, 1, 4 of layer 0 and 2, 3, 5 of layer 1
    assert cold_again["uploads"] == cold["uploads"]            # every load starts cold today
    assert filled["fill"] == (6, 6 << 20)
    assert filled["uploads"]["all"] == (0, 0) and flat["uploads"]["all"] == (0, 0)


def test_a_large_batch_does_not_read_the_cache(tool, tmp_path) -> None:
    # more than 32 tokens in a lookup: the engine reads the host experts, so nothing is uploaded and nothing recorded
    big = [(0, [e % 8 for e in range(2 * 40)])]
    path = write_trace(tmp_path / "big.trace", 1, 8, 2, big + [(0, [0, 1])])
    (_, cold, _, _, _, _), = tool.run([str(path)], 64)
    assert cold["large"] == 1 and cold["lookups"] == 1 and cold["tokens"] == 1
    assert cold["uploads"]["all"][0] == 2
    assert sum(cold["uses"].values()) == 2


def test_a_trace_copied_before_the_engine_closed_it_is_read_up_to_its_last_whole_lookup(tool, tmp_path, capsys) -> None:
    records = [(0, [0, 1]), (0, [2, 3])]
    whole = write_trace(tmp_path / "whole.trace", 1, 8, 2, records)
    cut = write_trace(tmp_path / "cut.trace", 1, 8, 2, records, tail=struct.pack("<3i", 0, 2, 2) + b"\x01\x00")
    assert tool.read_trace(str(cut))[3] == tool.read_trace(str(whole))[3]
    assert "cut short" in capsys.readouterr().err
    with pytest.raises(SystemExit, match="not a MoE cache trace"):
        tool.read_trace(str(ROOT / "CLAUDE.md"))


def test_the_loads_must_be_of_one_model(tool, tmp_path) -> None:
    a = write_trace(tmp_path / "a.trace", 1, 8, 2, [(0, [0, 1])])
    b = write_trace(tmp_path / "b.trace", 1, 16, 2, [(0, [0, 1])])
    with pytest.raises(SystemExit, match="another model"):
        tool.run([str(a), str(b)], 64)
    empty = write_trace(tmp_path / "empty.trace", 1, 8, 2, [])
    with pytest.raises(SystemExit, match="no lookup"):
        tool.run([str(empty)], 64)


def test_its_constants_are_the_patchs(tool) -> None:
    # the record's half-life is staged 0017's; the cache's are llama.cpp's (the tool says where each comes from)
    patch = PATCH.read_text()
    half_life = re.search(r"static constexpr float half_life = ([0-9.]+)f;", patch)
    assert half_life is not None and float(half_life.group(1)) == tool.RECORD_HALF_LIFE
    assert "moe_cache_lru::crf_half_life/llama_moe_record::half_life" in patch
    assert tool.ClassLRU.CRF_HALF_LIFE == 128.0 and tool.ClassLRU.N_CLASS == 8 and tool.MAX_BATCH == 32
    assert "scripts/moe-cache-tune.py" in TOOL.read_text() and "MIT" in TOOL.read_text()


def test_it_runs_as_the_note_says_with_the_standard_library_alone(tmp_path) -> None:
    paths = [str(p) for p in golden_loads(tmp_path)]
    done = subprocess.run([sys.executable, "-I", str(TOOL), "--mib", "49", *paths], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "cache 49 MiB: 48 slots" in done.stdout
    assert done.stdout.count("| cold ") == 4 and done.stdout.count("| filled ") == 3 and done.stdout.count("| flat ") == 3
    assert done.stdout.count("the record removes") == 6


def test_a_cap_on_the_seeds_lies_between_the_two(tool, tmp_path) -> None:
    # --cap N: the record's rank, no higher than the CRF N. A cap above every seed is 0017 as built; a cap of 1 is flat
    paths = [str(p) for p in golden_loads(tmp_path)]
    rows = tool.run(paths, 49, caps=(1.0, 4.0, 1e9))
    for row, golden in zip(rows[1:], GOLDEN[1:]):
        capped = row[4]
        assert uploads(capped[1.0]) == golden[9:13]
        assert uploads(capped[1e9]) == golden[4:8] == uploads(row[2])
    # on the other subject a cap of 4 costs less than the record's rank does (from the engine's own class: 1792 in all against 1846)
    assert uploads(rows[3][4][4.0]) == CAP_4_OTHER_SUBJECT
    done = subprocess.run([sys.executable, "-I", str(TOOL), "--mib", "49", "--cap", "4", *paths], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.count("| cap 4 ") == 3 and done.stdout.count("| flat ") == 3
    refused = subprocess.run([sys.executable, "-I", str(TOOL), "--mib", "49", "--cap", "0.5", *paths], capture_output=True, text=True)
    assert refused.returncode != 0 and "at least" in refused.stderr


def test_a_share_of_the_cache_filled_is_the_engines_share(tool, tmp_path) -> None:
    # --fill S is LLAMA_MOE_RECORD_FILL: the best-ranked experts, as many as that share of the slots, rounded down
    assert [tool.fill_slots(s, 4492) for s in (1.0, 0.5, 0.25, 0.0, 0.0001)] == [4492, 2246, 1123, 0, 0]
    paths = [str(p) for p in golden_loads(tmp_path)]
    rows = tool.run(paths, 49, shares=(1.0, 0.5, 0.0))
    assert rows[0][5] is None
    for row, golden, half in zip(rows[1:], GOLDEN[1:], HALF_FILL):
        shared = row[5]
        assert uploads(shared[1.0]) + [shared[1.0]["fill"][0]] == golden[4:9]      # the whole cache: 0017 unset
        assert uploads(shared[0.5]) + [shared[0.5]["fill"][0]] == half
        assert uploads(shared[0.0]) == golden[0:4] and shared[0.0]["fill"] == (0, 0)  # nothing filled: a cold cache
    done = subprocess.run([sys.executable, "-I", str(TOOL), "--mib", "49", "--fill", "0.5", "--fill", "0.25", *paths],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.count("| fill 0.5 ") == 3 and done.stdout.count("| fill 0.25 ") == 3
    assert done.stdout.count("MiB under 64 tokens for") == 12
    refused = subprocess.run([sys.executable, "-I", str(TOOL), "--mib", "49", "--fill", "1.5", *paths], capture_output=True, text=True)
    assert refused.returncode != 0 and "from 0 to 1" in refused.stderr
