"""The block origin measured on a synthetic wikidata-shaped dump
(docs/notes/wikidata-entity-origin.md, "verifying against the origin, by the
dump's blocks"): one bzip2 stream, the copy's block index built and timed,
and sampled records' blocks fetched from a loopback origin by the block
origin's own code (app/acquire/block_origin.py).

    python -m tools.measure.bz2_blocks_bench dump OUT ENTITIES [--level 9]
    python -m tools.measure.bz2_blocks_bench index DUMP [--threads N] [--stop-after S] [--segment BYTES]
    python -m tools.measure.bz2_blocks_bench compare COPY_ROOT RECORDS      (the dump at COPY_ROOT/<run's path>)

Run the timed ones under tools/measure/check_rss.py for their peak memory,
alone on the machine (docs/notes/measuring.md §10). Each prints one JSON
line. Entity lines are shaped as Wikidata's — labels, descriptions, aliases,
claims with content-hashed snaks, random statement GUIDs, shared references,
sitelinks — so the ratio (~10) and the block sizes (900 kB at level 9) are
near the real dump's (~14.5). Nothing is written but OUT and the index (under
LOUISE_CACHE_DIR, else `.pack/` beside the dump).
"""
from __future__ import annotations

import argparse
import bz2
import hashlib
import json
import os
import random
import sys
import threading
import time
from pathlib import Path

RUN = "20261012"
REL = f"wikidatawiki/entities/{RUN}/wikidata-{RUN}-all.json.bz2"


def _lines():
    from app import bz2_blocks
    return bz2_blocks.Lines(opens=b"{", head=b"[", tail=b"]")


class _Entities:
    LANGS = ("en", "fr", "de", "it", "es", "nl", "pt", "ru", "ja", "zh", "pl", "sv", "uk", "ca", "ar", "fa")
    SYL = ("ka", "ri", "to", "ne", "sa", "lo", "mi", "da", "ve", "ul", "an", "or", "es", "in", "ber", "stein",
           "ton", "ville", "berg", "ia", "us", "ova", "ski", "sen", "ar", "el", "mo", "zu", "pa", "qui")
    DESC = ("painting by", "species of insect", "human settlement in", "family name", "village in",
            "Wikimedia category", "river in", "album by", "street in", "politician", "footballer")
    PROPS = tuple(f"P{p}" for p in (31, 279, 17, 131, 625, 18, 569, 570, 21, 27, 106, 735, 734, 19, 20, 373, 214,
                                     227, 244, 268, 646, 2671, 1566, 856, 571, 580, 582, 585, 1082, 2044, 421, 910))

    def __init__(self, seed: int = 20261118) -> None:
        self.rng = r = random.Random(seed)
        self.popular = [5, 6581097, 6581072, 515, 486972, 532, 16521, 4167836, 11424, 482994, 7187] + \
            [r.randint(1, 3_000_000) for _ in range(3000)]
        self.words = ["".join(r.choice(self.SYL) for _ in range(r.randint(1, 4))) for _ in range(20000)]
        self.refs = []
        for _ in range(60):
            ref = {"snaks": {"P143": [self.snak("P143")]}, "snaks-order": ["P143"]}
            self.refs.append({"hash": hashlib.sha1(json.dumps(ref, sort_keys=True).encode()).hexdigest(), **ref})

    def hx(self, k: int) -> str:
        return f"{self.rng.getrandbits(4 * k):0{k}x}"

    def name(self) -> str:
        return " ".join(self.rng.choice(self.words).capitalize() for _ in range(self.rng.randint(1, 3)))

    def snak(self, prop: str) -> dict:
        r = self.rng
        kind = r.random()
        if kind < 0.55:
            num = r.randint(1, 120_000_000) if r.random() < 0.25 else \
                self.popular[int(r.paretovariate(1.2)) % len(self.popular)]
            dv, dt = {"value": {"entity-type": "item", "numeric-id": num, "id": f"Q{num}"},
                      "type": "wikibase-entityid"}, "wikibase-item"
        elif kind < 0.7:
            dv, dt = {"value": {"time": f"+{r.randint(1000, 2025)}-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}"
                                        f"T00:00:00Z", "timezone": 0, "before": 0, "after": 0,
                                "precision": r.choice((9, 11)),
                                "calendarmodel": "http://www.wikidata.org/entity/Q1985727"}, "type": "time"}, "time"
        elif kind < 0.85:
            dv, dt = {"value": str(r.randint(10_000, 99_999_999)), "type": "string"}, "external-id"
        elif kind < 0.93:
            dv, dt = {"value": {"latitude": r.uniform(-90, 90), "longitude": r.uniform(-180, 180), "altitude": None,
                                "precision": 0.0001, "globe": "http://www.wikidata.org/entity/Q2"},
                      "type": "globecoordinate"}, "globe-coordinate"
        else:
            dv, dt = {"value": {"amount": f"+{r.randint(1, 10**7)}", "unit": "1"}, "type": "quantity"}, "quantity"
        h = hashlib.sha1(json.dumps([prop, dv], sort_keys=True).encode()).hexdigest()
        return {"snaktype": "value", "property": prop, "hash": h, "datavalue": dv, "datatype": dt}

    def entity(self, i: int) -> dict:
        r = self.rng
        eid = f"Q{i}"
        langs = r.sample(list(self.LANGS), r.randint(1, len(self.LANGS)))
        base = self.name()
        labels = {lg: {"language": lg, "value": base if r.random() < 0.7 else self.name()} for lg in langs}
        claims = {}
        for p in r.sample(list(self.PROPS), r.randint(2, 18)):
            sts = []
            for _ in range(r.choice((1, 1, 1, 2, 3))):
                st = {"mainsnak": self.snak(p), "type": "statement",
                      "id": f"{eid}${self.hx(8)}-{self.hx(4)}-{self.hx(4)}-{self.hx(4)}-{self.hx(12)}",
                      "rank": "normal"}
                if r.random() < 0.6:
                    st["references"] = [self.refs[int(r.paretovariate(1.0)) % len(self.refs)]]
                if r.random() < 0.2:
                    st["qualifiers"] = {"P580": [self.snak("P580")]}
                    st["qualifiers-order"] = ["P580"]
                sts.append(st)
            claims[p] = sts
        return {"type": "item", "id": eid, "labels": labels,
                "descriptions": {lg: {"language": lg, "value": f"{r.choice(self.DESC)} {self.name()}"}
                                 for lg in langs[:r.randint(0, len(langs))]},
                "aliases": {lg: [{"language": lg, "value": self.name()} for _ in range(r.randint(1, 3))]
                            for lg in langs[:r.randint(0, 3)]},
                "claims": claims,
                "sitelinks": {f"{lg}wiki": {"site": f"{lg}wiki", "title": labels[lg]["value"], "badges": []}
                              for lg in langs[:r.randint(0, 4)]},
                "lastrevid": r.randint(10**8, 2 * 10**9),
                "modified": f"2026-{r.randint(1, 10):02d}-{r.randint(1, 28):02d}T12:00:00Z"}


def cmd_dump(a) -> dict:
    out, n = Path(a.out), int(a.entities)
    out.parent.mkdir(parents=True, exist_ok=True)
    gen = _Entities()
    t0 = time.monotonic()
    c = bz2.BZ2Compressor(int(a.level))
    raw = 0
    with open(out, "wb") as f:
        f.write(c.compress(b"[\n"))
        raw += 2
        batch: list[bytes] = []
        for i in range(1, n + 1):
            line = json.dumps(gen.entity(i), ensure_ascii=False, separators=(",", ":")).encode()
            batch.append(line + (b",\n" if i < n else b"\n"))
            if len(batch) >= 2000 or i == n:
                data = b"".join(batch) + (b"]\n" if i == n else b"")
                raw += len(data)
                f.write(c.compress(data))
                batch = []
        f.write(c.flush())
        size = f.tell()
    return {"entities": n, "decompressed": raw, "compressed": size, "ratio": round(raw / size, 2),
            "bytes_per_line": round(raw / max(n, 1)), "seconds": round(time.monotonic() - t0, 1)}


def cmd_index(a) -> dict:
    from app import bz2_blocks as bb
    dump = Path(a.dump)
    if a.segment:
        bb.SEGMENT = int(a.segment)
    t0 = time.monotonic()
    try:
        ix = bb.build(dump, _lines(), threads=a.threads,
                      deadline=(t0 + float(a.stop_after)) if a.stop_after else None)
    except bb.Incomplete as e:
        return {"incomplete": str(e), "seconds": round(time.monotonic() - t0, 2)}
    s = time.monotonic() - t0
    size = dump.stat().st_size
    return {"threads": int(ix.meta["threads"]), "seconds": round(s, 2), "blocks": ix.blocks, "units": ix.units,
            "framed": ix.framed(_lines()), "compressed_MBps": round(size / s / 1e6, 1),
            "decompressed_MBps": round(int(ix.meta["decompressed"]) / s / 1e6, 1),
            "index_bytes": ix.path.stat().st_size, "bytes_per_block": round(size / ix.blocks)}


def cmd_compare(a) -> dict:
    """N records drawn at random, their blocks compared with a loopback
    origin serving the copy by Range (os.pread), through BlockOrigin."""
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from types import SimpleNamespace

    from app.acquire import block_origin, specs
    copy = Path(a.copy_root)
    dump = copy / REL

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):
            pass

        def do_GET(self):
            p = copy / self.path.lstrip("/")
            size = p.stat().st_size
            lo, _, hi = self.headers["Range"].split("=", 1)[1].partition("-")
            lo, hi = int(lo), min(int(hi), size - 1)
            fd = os.open(p, os.O_RDONLY)
            try:
                data = os.pread(fd, hi - lo + 1, lo)
            finally:
                os.close(fd)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {lo}-{hi}/{size}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    class Plugin:
        LOCATOR_SEP = "#"

        def local_of(self, origin, base, run):
            return dump, f"{dump}#{origin.rpartition('#')[2]}"

    codex = SimpleNamespace(plugin="wikidata",
                            origin=SimpleNamespace(spec={"url": f"http://127.0.0.1:{srv.server_address[1]}"}))
    bo = block_origin.BlockOrigin(specs.WIKIDATA_SPEC, codex, Plugin(), _lines())
    try:
        why = bo.prepare(copy, RUN)
        if why is not None:
            return {"error": why}
        n = int(a.records)
        locs = [f"wikidatawiki/entities/all.json.bz2#{o}"
                for o in random.Random(7).sample(range(bo.index.units), n)]
        t0 = time.monotonic()
        units = dict(bo.unit(x) for x in locs)
        unit_s = time.monotonic() - t0
        keys = sorted({bo.blocks_of(x) for x in locs})
        t0 = time.monotonic()
        with ThreadPoolExecutor(bo.max_conns) as ex:
            out = list(ex.map(lambda k: bo.compare(*k), keys))
        cmp_s = time.monotonic() - t0
    finally:
        bo.close()
        srv.shutdown()
    return {"records": n, "units": len(units), "units_of_two_blocks": sum(1 for a, b in keys if b > a),
            "unit_bytes": sum(units.values()), "fetched": bo.bytes_read, "requests": bo.requests,
            "bytes_per_record": round(bo.bytes_read / n), "bytes_per_unit": round(bo.bytes_read / len(keys)),
            "all_same": all(o == ("same", None) for o in out),
            "unit_us_per_record": round(1e6 * unit_s / n, 1), "compare_s": round(cmp_s, 3),
            "compare_ms_per_unit": round(1e3 * cmp_s / len(keys), 2), "max_conns": bo.max_conns}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bz2_blocks_bench", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("out")
    d.add_argument("entities", type=int)
    d.add_argument("--level", type=int, default=9)
    i = sub.add_parser("index")
    i.add_argument("dump")
    i.add_argument("--threads", type=int, default=None)
    i.add_argument("--stop-after", dest="stop_after", type=float, default=None)
    i.add_argument("--segment", type=int, default=None)
    c = sub.add_parser("compare")
    c.add_argument("copy_root")
    c.add_argument("records", type=int)
    a = p.parse_args(argv)
    got = {"dump": cmd_dump, "index": cmd_index, "compare": cmd_compare}[a.cmd](a)
    print(json.dumps(got))
    return 1 if "error" in got else 0


if __name__ == "__main__":
    sys.exit(main())
