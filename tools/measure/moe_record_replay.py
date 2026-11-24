#!/usr/bin/env python3
"""What a record of the experts used would save, replayed from routing traces.

docs/notes/moe-expert-record.md. The engine's MoE cache starts every load empty
and uploads each expert at its first use; staged 0017 fills it, inside the load,
from a record of earlier use. This tool says how many of a load's uploads that
removes, BEFORE any engine is swapped, from what a live engine already writes:

    LLAMA_MOE_CACHE_TRACE=<file>   in a model's env, one file per load

a routing trace — per lookup, the layer and the experts selected; no text. Each
file is replayed as one load, three times: on a cache that starts EMPTY ("cold",
today); on one FILLED from the record of the files before it, each expert seeded
with the eviction rank its record gives it ("filled", staged 0017 as built); and
on one filled the same way with every expert seeded as if used once ("flat": a
wrong guess is the first to go). The first file only builds the record.
`--cap N` adds a line between the two: the record's rank, but no higher than the
CRF N (N = 1 is flat; 2, 4, 8 ... let a right guess hold its slot that much longer).

    python3 -I tools/measure/moe_record_replay.py --mib 8192 load1.trace load2.trace [load3.trace ...]

What it prints per load: the tokens; the experts uploaded under the first 16, 64
and 256 tokens and in all, for each of the three; what the fill itself uploads
(inside the load, not under a token); and the MiB. A load on another subject than
the record's shows what a wrong record COSTS: read its "in all" against cold's.

The cache logic is llama.cpp's own: `read_trace`, `n_slots_of` and `ClassLRU`
below are scripts/moe-cache-tune.py at the engine's pin (78e0529; MIT, (c)
2023-2026 The ggml authors), which mirrors src/llama-moe-cache.cpp with its
float32 rounding, copied so that the tool runs where the engine's sources are
not. Ours: the record (llama-moe-record.h's decay and choice), the fill and its
seeded eviction order (`ClassLRU.seed`, as moe_cache_lru::seed), the two replays.
No share of the misses on the CPU (the node's setting, and the only one for which
placement cannot change an answer).
"""
from __future__ import annotations

import argparse
import math
import struct
import sys

MAX_BATCH = 32            # llama_moe_cache::impl::max_batch
RECORD_HALF_LIFE = 4096.0  # llama_moe_record::half_life, tokens
FIRST = (16, 64, 256)


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def pad(x, n):
    return (x + n - 1) // n * n


# ---- from scripts/moe-cache-tune.py (llama.cpp, MIT) ---------------------------------------------

class Group:
    n_expert: int
    layers: list
    split: float
    split_sum: float
    host_bytes: int
    dev_host_bytes: int
    alignment: int
    nb2: list
    expert_bytes: int


def read_trace(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"MCT1":
        sys.exit(f"{path}: not a MoE cache trace")
    pos = 4

    def take(fmt):
        nonlocal pos
        v = struct.unpack_from("<" + fmt, data, pos)
        pos += struct.calcsize("<" + fmt)
        return v

    n_layer, n_expert_used, n_group = take("3I")
    groups = []
    for _ in range(n_group):
        g = Group()
        _, g.n_expert, n_layers = take("3I")
        g.layers = list(take(f"{n_layers}I"))
        g.split, g.split_sum = take("2d")
        g.host_bytes, g.dev_host_bytes, g.alignment = take("3Q")
        (n_tensors,) = take("I")
        g.nb2 = list(take(f"{n_tensors}Q"))
        g.expert_bytes = sum(g.nb2)
        groups.append(g)

    # ours: the engine closes the file when the context goes; one copied before that ends inside a record, which is
    # left out and said (upstream reads a whole file or fails)
    records = []
    cut = 0
    while pos < len(data):
        if len(data) - pos < 12:
            cut = len(data) - pos
            break
        il, n_used, n_ids = struct.unpack_from("<3i", data, pos)
        if n_used <= 0 or n_ids <= 0 or n_ids % n_used != 0 or not 0 <= il < n_layer:
            sys.exit(f"{path}: not a MoE cache trace (a lookup of {n_ids} ids, {n_used} per token, layer {il})")
        if len(data) - pos - 12 < 4 * n_ids:
            cut = len(data) - pos
            break
        pos += 12
        records.append((il, n_used, struct.unpack_from(f"<{n_ids}i", data, pos)))
        pos += 4 * n_ids
    if cut:
        print(f"{path}: the last {cut} bytes are a lookup cut short (the file was copied before the engine closed it); left out",
              file=sys.stderr)
    return n_layer, n_expert_used, groups, records


def n_slots_of(g, size_mib, n_zero, n_expert_used):
    budget = int(float(size_mib * 1024 * 1024) * g.split / g.split_sum * float(g.host_bytes) / float(g.dev_host_bytes))

    def alloc(n):
        return sum(pad(nb2 * (n + n_zero + 1), g.alignment) for nb2 in g.nb2)

    if alloc(0) > budget:
        return 0
    lo, hi = 0, len(g.layers) * g.n_expert
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if alloc(mid) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo if lo >= n_expert_used else 0


class ClassLRU:
    """moe_cache_lru, with its float32 rounding of the CRF"""
    N_CLASS = 8
    CRF_HALF_LIFE = 128.0  # tokens

    def __init__(self, layers, n_expert, n_slots):
        self.n_expert = n_expert
        self.n_slots = n_slots
        self.slot_map = {il: [n_slots] * n_expert for il in layers}
        self.key_of = [-1] * n_slots
        self.prev = [s - 1 for s in range(n_slots)]
        self.next = [s + 1 if s + 1 < n_slots else -1 for s in range(n_slots)]
        self.head = [0] + [-1] * (self.N_CLASS - 1)
        self.tail = [n_slots - 1] + [-1] * (self.N_CLASS - 1)
        self.list_of = [0] * n_slots
        self.n_filled = 0
        self.crf = {}       # key -> CRF, 0 before the first use
        self.last_use = {}  # key -> layer clock at the last use
        self.clock = {il: 0 for il in layers}
        self.seen = [0] * n_expert
        self.seen_gen = 0
        self.n_use = {}     # ours: key -> lookups that selected the expert, for the record

    def move(self, s, c):
        frm = self.list_of[s]
        if frm == c and s == self.tail[frm]:
            return
        prev, nxt, head, tail = self.prev, self.next, self.head, self.tail
        if prev[s] >= 0:
            nxt[prev[s]] = nxt[s]
        else:
            head[frm] = nxt[s]
        if nxt[s] >= 0:
            prev[nxt[s]] = prev[s]
        else:
            tail[frm] = prev[s]
        prev[s] = tail[c]
        nxt[s] = -1
        if tail[c] >= 0:
            nxt[tail[c]] = s
        else:
            head[c] = s
        tail[c] = s
        self.list_of[s] = c

    def class_of(self, key):
        return min(self.N_CLASS - 1, int(f32(math.log2(self.crf[key]))))  # (int) log2f

    def decayed_crf(self, il, key):
        crf = self.crf.get(key, 0.0)
        if crf == 0.0:
            return 0.0
        x = f32(f32(-(self.clock[il] - self.last_use[key])) / self.CRF_HALF_LIFE)
        return f32(crf * f32(2.0 ** x))

    def use(self, il, key):
        self.crf[key] = f32(self.decayed_crf(il, key) + 1.0)
        self.last_use[key] = self.clock[il]
        self.n_use[key] = self.n_use.get(key, 0) + 1

    def seed(self, il, e, s, crf_seed):
        """ours, as moe_cache_lru::seed: expert e of layer il takes the unused slot s before any token"""
        assert 0 <= s < self.n_slots and self.key_of[s] < 0 and self.slot_map[il][e] == self.n_slots
        key = il * self.n_expert + e
        self.crf[key] = f32(max(1.0, crf_seed))
        self.last_use[key] = self.clock[il]
        self.key_of[s] = key
        self.slot_map[il][e] = s
        self.n_filled += 1
        self.move(s, self.class_of(key))

    def is_selected(self, il, key):
        return key >= 0 and key // self.n_expert == il and self.seen[key % self.n_expert] == self.seen_gen

    def victim(self, il):
        res, res_score = -1, -1.0
        for c in range(self.N_CLASS):
            s = self.head[c]
            while s >= 0 and self.is_selected(il, self.key_of[s]):
                s = self.next[s]
            if s < 0:
                continue
            key = self.key_of[s]
            if key < 0:
                return s  # unused slot
            score = math.ldexp(float(self.clock[key // self.n_expert] - self.last_use[key]), -c)  # age/2^class
            if score > res_score:
                res, res_score = s, score
        if res < 0:
            raise RuntimeError("no MoE cache victim")
        return res

    def plan(self, il, ids, n_used):
        """returns the experts uploaded (no share of the misses on the CPU)"""
        self.seen_gen += 1
        seen, gen = self.seen, self.seen_gen
        uniq = []
        for e in ids:
            if seen[e] != gen:
                seen[e] = gen
                uniq.append(e)
        if len(uniq) > self.n_slots:
            raise RuntimeError(f"layer {il}: {len(uniq)} experts for {self.n_slots} slots")

        slots = self.slot_map[il]
        n_slots = self.n_slots
        n_tokens = len(ids) // n_used
        misses = []
        for e in uniq:
            key = il * self.n_expert + e
            self.use(il, key)
            if slots[e] < n_slots:
                self.move(slots[e], self.class_of(key))
            else:
                misses.append(e)

        misses.sort()
        for e in misses:
            s = self.victim(il)
            key = self.key_of[s]
            if key >= 0:
                self.slot_map[key // self.n_expert][key % self.n_expert] = n_slots
            else:
                self.n_filled += 1
            key = il * self.n_expert + e
            self.key_of[s] = key
            slots[e] = s
            self.move(s, self.class_of(key))
        self.clock[il] += n_tokens
        return len(misses)


# ---- ours ----------------------------------------------------------------------------------------

class Record:
    """llama_moe_record: per key, the lookups that selected the expert, decayed over RECORD_HALF_LIFE tokens of its layer"""

    def __init__(self, n_expert):
        self.n_expert = n_expert
        self.score = {}  # key -> float32
        self.n_tokens = 0

    def add(self, uses, layer_tokens):
        for il, t in layer_tokens.items():
            if t <= 0:
                continue
            decay = f32(2.0 ** f32(-float(t) / RECORD_HALF_LIFE))
            for key in [k for k in self.score if k // self.n_expert == il]:
                self.score[key] = f32(self.score[key] * decay)
            self.n_tokens += t
        for key, n in uses.items():
            if layer_tokens.get(key // self.n_expert, 0) > 0:
                self.score[key] = f32(self.score.get(key, 0.0) + float(n))

    def best(self, layers, n):
        layers = set(layers)
        keys = [k for k, s in self.score.items() if s > 0.0 and k // self.n_expert in layers]
        keys.sort(key=lambda k: (-self.score[k], k))
        return keys[:n]


def make_caches(trace, size_mib):
    _, n_expert_used, groups, _ = trace
    caches, group_of = [], {}
    for ig, g in enumerate(groups):
        n = n_slots_of(g, size_mib, 0, n_expert_used)
        caches.append(ClassLRU(g.layers, g.n_expert, n) if n > 0 else None)
        group_of.update((il, ig) for il in g.layers)
    return caches, group_of


def fill(caches, groups, record, cap=None):
    """llama_moe_cache::impl::fill_from_record: returns (experts, bytes) uploaded by the fill.
    cap: an expert is seeded with its record's CRF as 0017 seeds it, but no higher than this; 1 is the CRF of one use"""
    to_crf = ClassLRU.CRF_HALF_LIFE / RECORD_HALF_LIFE
    n_experts = n_bytes = 0
    for g, c in zip(groups, caches):
        if c is None or g.n_expert != record.n_expert:
            continue
        keys = record.best(g.layers, c.n_slots)
        slot_of = {key: i for i, key in enumerate(sorted(keys))}
        for key in reversed(keys):  # the least used first: within a class the most used is evicted last
            crf = f32(record.score[key] * to_crf)
            c.seed(key // g.n_expert, key % g.n_expert, slot_of[key], crf if cap is None else min(f32(cap), max(1.0, crf)))
        n_experts += len(keys)
        n_bytes += len(keys) * g.expert_bytes
    return n_experts, n_bytes


def replay(trace, size_mib, record=None, cap=None):
    """one load: {'tokens', 'lookups', 'uploads': {16: (experts, bytes), 64: ..., 256: ..., 'all': ...}, 'fill': (experts, bytes),
    'large': lookups that did not read the cache, 'uses', 'layer_tokens'}"""
    n_layer, n_expert_used, groups, records = trace
    caches, group_of = make_caches(trace, size_mib)
    filled = fill(caches, groups, record, cap) if record is not None else (0, 0)
    # a token is counted where its first layer is looked up (the layers of a batch come in order)
    first = min((il for il, _, _ in records if caches[group_of[il]] is not None), default=-1)
    tokens = lookups = large = 0
    up = {n: [0, 0] for n in (*FIRST, "all")}
    for il, n_used, ids in records:
        n_tokens = len(ids) // n_used
        g, c = groups[group_of[il]], caches[group_of[il]]
        if c is None or n_tokens > MAX_BATCH or min(n_tokens * n_used, g.n_expert) > c.n_slots:
            large += 1  # the engine reads the host experts for such a batch: the cache is not asked
            continue
        if il == first:
            tokens += n_tokens
        lookups += 1
        n_fill = c.plan(il, ids, n_used)
        for n in up:
            if n == "all" or tokens <= n:
                up[n][0] += n_fill
                up[n][1] += n_fill * g.expert_bytes
    uses, layer_tokens = {}, {}
    n_expert = groups[0].n_expert  # the model's, as far as a trace says it
    for g, c in zip(groups, caches):
        if c is not None and g.n_expert == n_expert:
            uses.update(c.n_use)
            layer_tokens.update(c.clock)
    return {"tokens": tokens, "lookups": lookups, "large": large, "uploads": {n: tuple(v) for n, v in up.items()},
            "fill": filled, "uses": uses, "layer_tokens": layer_tokens,
            "slots": sum(c.n_slots for c in caches if c is not None)}


def run(paths, size_mib, caps=(1.0,)):
    """[(path, cold, filled or None, capped or None)]: each file one load, the record built from the loads before it;
    capped is the replay with the first of `caps` (flat by default), row[4] a dict of all of them by cap"""
    out, record, shape = [], None, None
    for path in paths:
        trace = read_trace(path)
        this = (trace[0], trace[1], [(g.n_expert, g.layers, g.nb2) for g in trace[2]])
        if shape is not None and this != shape:
            sys.exit(f"{path}: the trace of another model or another placement than {paths[0]}")
        shape = this
        if not trace[3]:
            sys.exit(f"{path}: no lookup in it - was an answer generated before the file was copied?")
        cold = replay(trace, size_mib)
        filled = replay(trace, size_mib, record) if record is not None else None
        capped = {cap: replay(trace, size_mib, record, cap=cap) for cap in caps} if record is not None else None
        out.append((path, cold, filled, capped[caps[0]] if capped else None, capped))
        if record is None:
            record = Record(trace[2][0].n_expert)
        # the record grows from what a cache saw; a filled and a cold cache see the same lookups
        record.add(cold["uses"], cold["layer_tokens"])
    return out


def mib(n_bytes):
    return n_bytes / 2 ** 20


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("traces", nargs="+", help="LLAMA_MOE_CACHE_TRACE files, one per load, in the order they were made")
    ap.add_argument("--mib", type=int, required=True, help="the model's moe_cache_mib")
    ap.add_argument("--cap", type=float, action="append", default=[], help="also replay with the seeds capped at this CRF (repeatable)")
    args = ap.parse_args(argv)
    if any(cap < 1.0 for cap in args.cap):
        ap.error("--cap: a seed is at least the CRF of one use, 1")
    caps = tuple(dict.fromkeys([1.0, *args.cap]))

    def name_of(cap):
        return "flat" if cap == 1.0 else f"cap {cap:g}"

    rows = run(args.traces, args.mib, caps)
    slots = rows[0][1]["slots"]
    print(f"cache {args.mib} MiB: {slots} slots; record half-life {RECORD_HALF_LIFE:.0f} tokens")
    print("load | tokens | cache | uploads under the first 16 / 64 / 256 tokens | in all | MiB in all | the fill (inside the load)")
    for i, (path, cold, filled, _, capped) in enumerate(rows, 1):
        def line(name, r):
            u = r["uploads"]
            fill_said = f"{r['fill'][0]} experts, {mib(r['fill'][1]):.0f} MiB" if name != "cold" else "-"
            print(f"{i:4d} | {r['tokens']:6d} | {name:7s} | {u[16][0]:6d} / {u[64][0]:6d} / {u[256][0]:6d} | {u['all'][0]:6d} | "
                  f"{mib(u['all'][1]):9.0f} | {fill_said}")

        def said(name, r):
            c64, call = cold["uploads"][64][0], cold["uploads"]["all"][0]
            d64, dall = c64 - r["uploads"][64][0], call - r["uploads"]["all"][0]
            print(f"     {name}: the record removes {d64} of {c64} uploads under the first 64 tokens "
                  f"({100.0 * d64 / max(c64, 1):.0f} %), {dall} of {call} in all ({100.0 * dall / max(call, 1):.0f} %)")
        line("cold", cold)
        if filled is not None:
            line("filled", filled)
            for cap in sorted(caps, reverse=True):
                line(name_of(cap), capped[cap])
            said("filled", filled)
            for cap in sorted(caps, reverse=True):
                said(name_of(cap), capped[cap])
        if cold["large"]:
            print(f"     ({cold['large']} lookups of large batches read the host experts, cache or not)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
