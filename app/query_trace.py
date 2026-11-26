"""THE ADMIN'S QUESTIONS, TRACED (operator, 2026-11-26, verbatim: "Log admin
only queries with timestamps for timing analysis. Include which models are
loaded, system and gpu memory usage, cpu load, load/unload/parking of the
models, together with query and results for a full analysis of both speed
and quality").

ONE JSON LINE PER QUESTION of an admin, in `state/query-traces/<UTC
day>.jsonl`, wherever it is asked: the Ask page's relay (a search, an
answer) and its `/ask/query`, `/v1/chat/completions`, `ask` over HTTP
(`/api/ask/search|chat|pdf|lookup|traverse`) and MCP (`/api/ask/mcp`, and
`ask serve`'s stdio, which only the node's operator can run). Joined by
`search_id` (an answer that reused the page's search names it). A line:

- `kind`, `job`, `who`, `query`, `search_id`, `started` / `ended` (UTC ISO,
  ms) and `ms` (the job's length);
- `marks`: [name, ms from the start] — `search_done`, `first_word`,
  `last_word`, `cut`, `error` — and `timings`, the search's own per stage
  (app/retrieval.py: languages, catalog, fts, dense, rerank, per collection);
- `samples`: the node every `SAMPLE_S` from the start to the end — `ms`,
  CPU busy % since the sample before (/proc/stat; the first is taken
  `FIRST_S` in, so it has an interval), the load average, RAM total /
  available / swap used (/proc/meminfo), and per card its VRAM used and —
  where it changed since the sample before — each process's VRAM, host GTT
  and label (the GPU probe, GPU_PROBE_URL); the first and the last sample
  also carry `models`: per model server (the chat, rerank and translation
  servers and every collection's encoders), what `/system` says is loaded and
  what is parked, from model_load's cache (never a read of its own that a
  question's fresh read would wait behind);
- `model_events`: the node's `models` lines written meanwhile, by the web and
  by the worker (cold starts, loads, evictions, parks and returns, pins:
  db.NODE_STAGES `models`, read from the events table they are written to);
- `results`: the hits as ranked — collection, id, title, the fused and the
  reranker's score, `moved_by`, language and the first characters of the
  passage — and, for an answer, `answer`: its text as written, and why it
  was cut when it was.

/proc is the host's in a container (loadavg, stat and meminfo are not
namespaced), which is the node's view a timing needs.

WHO WRITES. The question's own thread only marks times (`mark`, a list
append); the sampler — one daemon thread per trace — reads the node and
writes the line once the job ends (CLAUDE.md: telemetry is published by
someone other than the thread doing the work). A trace NEVER RAISES into its
question: every read that fails is recorded as its error and the question
goes on.

KEPT `KEEP_DAYS` days: the oldest files go when a new day's is opened.

THE STANDARD this is not: OpenTelemetry traces (spans with attributes,
exported over OTLP) say the same — a span per stage, resource metrics beside
— and pay once a collector is run (Tempo, Jaeger) and more than one process
is traced end to end. Today it would be a dependency and a service for one
admin's questions; the JSON lines map onto spans one to one if it is adopted.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from app import config

#: how often the node is sampled while a question runs, and when the first is
SAMPLE_S = 1.0
FIRST_S = 0.1
#: the days of traces kept
KEEP_DAYS = 30
#: the hits of a result kept, and the characters of each passage
HITS_KEPT = 50
TEXT_CHARS = 400
#: a trace that never ends (a job lost) is written after this long
MAX_S = 30 * 60.0

HIT_KEYS = ("source_collection", "collection", "uid", "book_id", "chunk_id", "title", "authors", "lang",
            "rrf", "score", "rerank_score", "moved_by", "chapter", "origin_url")


def is_admin(owner: str | None) -> bool:
    """A relay job's owner (web._relay_owner: "<role>:<name>") is an admin."""
    return bool(owner) and str(owner).split(":", 1)[0] == "admin"


def trace_dir() -> Path:
    return config.louise_root() / "state" / "query-traces"


def _iso(t: float) -> str:
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat(timespec="milliseconds")


# ---- the node, read ---------------------------------------------------------

def _cpu_times(path: Path = Path("/proc/stat")) -> tuple[int, int] | None:
    """(busy, total) jiffies of all CPUs."""
    try:
        first = path.read_text().splitlines()[0].split()
    except (OSError, IndexError):
        return None
    if not first or first[0] != "cpu":
        return None
    nums = [int(x) for x in first[1:]]
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)        # idle + iowait
    return sum(nums) - idle, sum(nums)


def _meminfo(path: Path = Path("/proc/meminfo")) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        for line in path.read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree", "Cached"):
                out[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return out


def _loadavg(path: Path = Path("/proc/loadavg")) -> list[float] | None:
    try:
        return [float(x) for x in path.read_text().split()[:3]]
    except (OSError, ValueError):
        return None


def _gpus() -> list[dict] | dict:
    from app import gpu
    got = gpu.panel(config.GPU_PROBE_URL)
    if got.get("state") != "ok":
        return {"state": got.get("state"), "error": got.get("error")}
    return [{"card": d.card, "name": d.name, "vram_total": d.vram_total, "vram_used": d.vram_used,
             "processes": [{"label": p.label, "container": p.container, "vram": p.vram, "host": p.host,
                            "ram": p.ram} for p in d.processes]}
            for d in got.get("devices") or ()]


def _changed_only(cards, before: dict[str, Any]) -> Any:
    """Each card's process list only where it differs from the sample before
    (`before`: card -> its last list, updated); else `processes_same`."""
    if not isinstance(cards, list):
        return cards
    out = []
    for c in cards:
        c = dict(c)
        procs = c.get("processes")
        if procs == before.get(c.get("card")):
            c.pop("processes", None)
            c["processes_same"] = True
        else:
            before[c.get("card")] = procs
        out.append(c)
    return out


def _collections() -> list:
    from app import collections as collections_mod
    try:
        return collections_mod.load_all()
    except Exception:   # noqa: BLE001 — the node's own servers still
        return []


def _servers(cols: list | None = None) -> list[str]:
    """The model servers a question meets: chat, rerank, translation, and
    every collection's encoders and reranker (gpu_models.origins)."""
    from app import gpu_models, model_load
    cols = _collections() if cols is None else cols
    out: list[str] = []
    for u in gpu_models.origins(cols, [config.CHAT_URL, config.RERANK_URL, config.TRANSLATE_URL]):
        s = model_load.server_of(u)
        if s not in out:
            out.append(s)
    return out


def _models(servers: list[str] | None = None) -> dict[str, Any]:
    """Per model server: what `/system` last said is loaded, how old that
    reading is (`age_s`; null: never read), and parked. The cache only
    (model_load.cached_models): a fetch would hold the per-server lock a
    question's own fresh read waits behind (0.95 s measured)."""
    from app import model_load
    out: dict[str, Any] = {}
    for s in (_servers() if servers is None else servers):
        try:
            got = model_load.cached_models(s)
            loaded, age = got if got is not None else (None, None)
            out[s] = {"loaded": sorted(loaded) if loaded is not None else None, "age_s": age,
                      "parked": sorted(model_load.parked(s))}
        except Exception as e:   # noqa: BLE001 — recorded, never raised
            out[s] = {"error": f"{type(e).__name__}: {e}"[:200]}
    return out


def _model_events(since: float, until: float, collection: Any = None) -> list[list]:
    """The node's `models` lines from `since` to `until` (seconds), as
    [UTC ISO, level, message]: the events table of the first collection,
    where model_load and model_pins write them (a table bounded at 5,000
    rows, db.log_event)."""
    from app import db
    if collection is None:
        cols = _collections()
        if not cols:
            return []
        collection = cols[0]
    with closing(db.connect(collection)) as con:
        rows = con.execute("SELECT ts, level, message FROM events WHERE stage = 'models' "
                           "AND ts >= ? AND ts <= ? ORDER BY id",
                           (int(since), int(until) + 1)).fetchall()
    return [[_iso(r[0]), r[1], r[2]] for r in rows]


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:   # noqa: BLE001 — a trace never takes its question down
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def hits_of(result: Any) -> list[dict] | dict:
    """The hits as ranked; a result of another shape (ask over HTTP, MCP)
    kept as JSON, cut at RESULT_CHARS. Never raises (`_safe`)."""
    got = _safe(_hits_of, result)
    return got


#: a result that is not a hit list, kept to this many characters of JSON
RESULT_CHARS = 20_000


def _hits_of(result: Any) -> list[dict] | dict:
    if not (isinstance(result, dict) and isinstance(result.get("hits"), list)):
        text = json.dumps(result, ensure_ascii=False, default=str)
        return {"json": text[:RESULT_CHARS], "chars": len(text)}
    out = []
    for rank, h in enumerate((result or {}).get("hits") or [], 1):
        if not isinstance(h, dict):
            continue
        row = {"rank": rank, **{k: h[k] for k in HIT_KEYS if h.get(k) not in (None, "", [])}}
        text = h.get("text") or h.get("snippet")
        if text:
            row["text"] = str(text)[:TEXT_CHARS]
        out.append(row)
        if len(out) >= HITS_KEPT:
            break
    return out


# ---- one question -----------------------------------------------------------

class Trace:
    """A question job of an admin, sampled while it runs and written when it
    ends. `mark` and `note` are what the question's thread calls."""

    def __init__(self, kind: str, job: str, who: str, query: str, *, search_id: str | None = None,
                 sample_s: float | None = None) -> None:
        self.kind, self.job, self.who, self.query = kind, job, who, query
        self.search_id = search_id
        self.t0 = time.time()
        self._m0 = time.monotonic()
        self.marks: list[list] = []
        self.data: dict[str, Any] = {}
        self.samples: list[dict] = []
        self._answer: list[str] = []
        self._ended = threading.Event()
        self._t1: float | None = None
        # the directory resolved NOW, on the question's thread: the sampler
        # writes after the request has returned, when a test's LOUISE_ROOT
        # may already be undone (architecture, 2026-11-26: a test's line in /data)
        self._dir = trace_dir()
        # the node's collections and model servers, read here for the same
        # reason: the sampler reads the node after the request has returned
        cols = _collections()
        self._servers = _safe(_servers, cols)
        self._events_of = cols[0] if cols else None
        self._cards: dict[str, Any] = {}
        self._sample_s = SAMPLE_S if sample_s is None else sample_s
        self._cpu = _cpu_times()
        self._thread = threading.Thread(target=self._run, name=f"query-trace-{job}", daemon=True)
        with _live_lock:
            _live.add(self)
        self._thread.start()

    def ms(self) -> float:
        return round((time.monotonic() - self._m0) * 1000, 1)

    def mark(self, name: str) -> None:
        self.marks.append([name, self.ms()])

    def note(self, **data: Any) -> None:
        """Facts of the question; never raises into it."""
        try:
            self.data.update({k: json.loads(json.dumps(v, default=str)) for k, v in data.items()})
        except Exception as e:   # noqa: BLE001
            self.data.setdefault("note_errors", []).append(f"{type(e).__name__}: {e}"[:200])

    def word(self, text: str) -> None:
        """A delta of the answer: its first and last times, and its text."""
        if not self._answer:
            self.mark("first_word")
        self._answer.append(text)
        self._last_word = self.ms()

    def end(self, error: str | None = None) -> None:
        """The question is over: its end time is now, not when the sampler
        has read the node once more."""
        if self._ended.is_set():
            return
        self._t1 = time.time()
        if self._answer:
            self.marks.append(["last_word", getattr(self, "_last_word", self.ms())])
            self.data["answer"] = "".join(self._answer)
        if error:
            self.mark("error")
            self.data["error"] = error[:500]
        self._ended.set()

    # -- the sampler's thread --
    def _sample(self, with_models: bool) -> dict:
        s: dict[str, Any] = {"ms": self.ms()}
        now = _cpu_times()
        if now and self._cpu and now[1] > self._cpu[1]:
            s["cpu_pct"] = round(100.0 * (now[0] - self._cpu[0]) / (now[1] - self._cpu[1]), 1)
        self._cpu = now or self._cpu
        s["load"] = _loadavg()
        mem = _meminfo()
        if mem:
            s["ram"] = {"total": mem.get("MemTotal"), "available": mem.get("MemAvailable"),
                        "swap_used": (mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) or 0}
        s["gpus"] = _changed_only(_safe(_gpus), self._cards)
        if with_models:
            s["models"] = (_safe(_models, self._servers) if isinstance(self._servers, list)
                           else {"error": self._servers})
        return s

    def _run(self) -> None:
        self._ended.wait(FIRST_S)              # the first CPU % has an interval
        self.samples.append(self._sample(True))
        deadline = time.monotonic() + MAX_S
        while not self._ended.wait(self._sample_s):
            if time.monotonic() > deadline:
                self.data.setdefault("error", f"the job did not end within {MAX_S / 60:.0f} min")
                break
            self.samples.append(self._sample(False))
        self.samples.append(self._sample(True))
        try:
            write(self.record(), root=self._dir)
        except Exception as e:   # noqa: BLE001 — said, never raised
            # stderr: in `ask serve` stdout is the JSON-RPC channel
            print(f"{time.strftime('%H:%M:%S')} [node warn:ask] query trace of {self.job} not written: {e}",
                  file=sys.stderr, flush=True)
        finally:
            with _live_lock:
                _live.discard(self)

    def record(self) -> dict:
        """The line. A question that never ended (MAX_S) has no end: `ended`
        and `ms` are null, and its error says so."""
        t1 = self._t1
        return {"kind": self.kind, "job": self.job, "who": self.who, "query": self.query,
                "search_id": self.search_id, "started": _iso(self.t0),
                "ended": _iso(t1) if t1 is not None else None,
                "ms": round((t1 - self.t0) * 1000, 1) if t1 is not None else None,
                # in time order: `last_word` is the last delta's time, put in at the end
                "marks": sorted(list(self.marks), key=lambda m: m[1]), **dict(self.data), "samples": self.samples,
                "model_events": (_safe(_model_events, self.t0 - 1, t1 if t1 is not None else time.time(),
                                       self._events_of) if self._events_of is not None else [])}

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)


#: the traces whose line is not written yet: a process that ends (`ask serve`
#: at the end of its input) writes them first (`flush`)
_live: set[Trace] = set()
_live_lock = threading.Lock()


def flush(timeout: float = 5.0) -> None:
    """Every trace still sampling — its question over — written before the
    process exits (its sampler is a daemon thread, which exit would kill)."""
    with _live_lock:
        live = list(_live)
    deadline = time.monotonic() + timeout
    for t in live:
        t.join(max(0.0, deadline - time.monotonic()))


def start(kind: str, job: str, who: str | None, query: str, *, admin: bool | None = None,
          **kw: Any) -> Trace | None:
    """A trace for an admin's question; None for anyone else (nothing is
    read). `admin`: the viewer's own verdict (auth.admin); None reads it from
    a relay owner's role (`is_admin`)."""
    if not (admin if admin is not None else is_admin(who)):
        return None
    try:
        return Trace(kind, job, str(who), query, **kw)
    except Exception:   # noqa: BLE001 — no trace, the question as before
        return None


def viewer_of(request: Any) -> tuple[str, bool]:
    """(who, admin) of an HTTP request (the auth middleware's viewer)."""
    from app import auth
    v = getattr(getattr(request, "state", None), "viewer", None)
    who = f"{v.role}:{v.name}" if v is not None else "nobody"
    return who, auth.admin(v)


def run(kind: str, who: str, admin: bool, query: str, fn, /, *args: Any, **kw: Any) -> Any:
    """`fn(*args, **kw)`, an admin's traced: its result kept (`hits_of`) and
    an exception recorded, then raised as it was."""
    import secrets
    trace = start(kind, secrets.token_hex(6), who, query, admin=admin)
    if trace is None:
        return fn(*args, **kw)
    try:
        out = fn(*args, **kw)
    except BaseException as e:
        trace.end(error=f"{type(e).__name__}: {e}")
        raise
    trace.note(results=hits_of(out))
    trace.end()
    return out


_write_lock = threading.Lock()


def write(record: dict, root: Path | None = None) -> Path:
    d = root if root is not None else trace_dir()
    d.mkdir(parents=True, exist_ok=True)
    day = record["started"][:10]
    path = d / f"{day}.jsonl"
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
    with _write_lock:
        fresh = not path.exists()
        with path.open("a+b") as f:
            # a line a full disk cut short is ended first, so this one stays whole
            if f.seek(0, 2) > 0:
                f.seek(-1, 2)
                if f.read(1) != b"\n":
                    f.write(b"\n")
            f.write(line.encode("utf-8"))
        if fresh:
            _prune(d, day)
    return path


def _prune(d: Path, today: str) -> None:
    cut = (_dt.date.fromisoformat(today) - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    for f in d.glob("*.jsonl"):
        if f.stem < cut:
            try:
                f.unlink()
            except OSError:
                pass
