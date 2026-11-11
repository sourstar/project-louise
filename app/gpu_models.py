"""The System page's GPU panel, by model: what each model loaded in LocalAI
holds on the card, and whether Louise or someone else uses it.

LocalAI is the only owner of the card's memory (docs/notes/vega.md), and its
`GET /system` lists every loaded model with its backend, its process and
`size_vram` — read from the driver's per-process counters, the same ones the
probe reads (measured 2026-10-12: equal to the MiB for all eight models, and
their sum 35 MiB under the card's own count). So the panel needs no
privilege to read other processes: it asks each LocalAI Louise points at.

"Louise" is a model a collection file names (`embed_model`, `query_model`,
`rerank_model`) or Louise's chat or translation model; every other model is
the operator's own. What the card counts and no loaded model holds is
"outside LocalAI": the driver's own buffers, or another program.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

#: per LocalAI asked; the panel reloads every 5 s
TIMEOUT_S = 1.5


@dataclass(frozen=True)
class ModelRow:
    model: str
    backend: str
    vram: int                  # bytes on the card, LocalAI's `size_vram`
    uses: tuple[str, ...]      # "gutenberg: embedding", "chat", … — empty: not Louise's
    #: its backend's pid as LocalAI gives it (its own namespace's): the GPU
    #: probe's `ns_pid` places it on a card (`by_card`); None not given
    pid: int | None = None

    @property
    def louise(self) -> bool:
        return bool(self.uses)


def louise_uses(collections, *, chat_model: str = "", translate_model: str = "",
                reading: dict[str, str] | None = None,
                speech: dict[str, str] | None = None) -> dict[str, list[str]]:
    """model name -> what Louise uses it for, from the collection files, the
    chat and translation settings and `[speech]` ({use: model})."""
    out: dict[str, list[str]] = {}

    def add(model, what):
        if model:
            out.setdefault(str(model), [])
            if what not in out[str(model)]:
                out[str(model)].append(what)
    for c in collections:
        e = getattr(c, "embed", None)
        if e is not None:
            same = e.query_model in (None, e.model)
            add(e.model, f"{c.name}: embedding" + (" and queries" if same else ""))
            if not same:
                add(e.query_model, f"{c.name}: queries")
        r = getattr(c, "reranker", None)
        if r is not None and getattr(r, "model", None):
            add(r.model, f"{c.name}: reranking")
    add(chat_model, "chat")
    add(translate_model, "translation")
    for src, model in sorted((reading or {}).items()):
        add(model, f"translation (reading {src})")
    for use, model in (speech or {}).items():
        add(model, use)
    return out


def origins(collections, extra_urls=()) -> list[str]:
    """Every distinct scheme://host:port Louise sends model requests to."""
    urls = []
    for c in collections:
        e = getattr(c, "embed", None)
        if e is not None:
            urls += [e.url, e.query_url]
        r = getattr(c, "reranker", None)
        if r is not None:
            urls.append(r.url)
    urls += list(extra_urls)
    seen: list[str] = []
    for u in urls:
        if not u:
            continue
        p = urlsplit(str(u))
        if p.scheme and p.netloc:
            o = f"{p.scheme}://{p.netloc}"
            if o not in seen:
                seen.append(o)
    return seen


def rows_from(system: dict, uses: dict[str, list[str]], seen: set | None = None) -> list[ModelRow]:
    """A LocalAI `/system` answer as rows. `seen` holds (model, pid, start)
    of rows already taken: two addresses that reach ONE LocalAI (a name and
    an IP, a path prefix) answer the same models, counted once."""
    seen = set() if seen is None else seen
    rows = []
    for m in (system.get("loaded_models") or []):
        proc = m.get("process") or {}
        key = (str(m.get("id")), proc.get("pid"), proc.get("started_at"))
        if key in seen:
            continue
        seen.add(key)
        pid = proc.get("pid")
        rows.append(ModelRow(model=str(m.get("id") or "?"), backend=str(m.get("backend") or ""),
                             vram=int(m.get("size_vram") or 0),
                             uses=tuple(uses.get(str(m.get("id")), ())),
                             pid=pid if isinstance(pid, int) and not isinstance(pid, bool) else None))
    return rows


def by_card(models: dict, devices) -> dict:
    """Each model ON ITS CARD (operator, 2026-11-11: "Add a CPU card with the
    models loaded"; a model on the processor's Radeon is the Radeon's, not the
    B70's): `{pdev: the card's own breakdown, as `panel` gives the whole one,
    ..., "cpu": [rows]}`.
    - A model whose backend pid the probe lists on a card (`ns_pid`, the pid
      as LocalAI numbers it) is that card's; listed on several, the card
      where it holds the most.
    - Not placed, holding VRAM, with ONE card: that card's.
    - Not placed, holding VRAM, with several: "unplaced", never the largest
      card by guess (the node, 2026-11-11: the iGPU's speech model shown on
      the B70 while the probe could read no process at all; the probe's
      note says why).
    - Not placed, holding none: the CPU's — a backend with no memory on any
      card runs on the processor."""
    rows = list(models.get("rows") or [])
    held: dict[int, tuple[int, str]] = {}
    for d in devices or ():
        for p in getattr(d, "processes", ()) or ():
            if getattr(p, "ns_pid", None) is not None:
                size = int(getattr(p, "vram", 0) or 0) + int(getattr(p, "host", 0) or 0)
                if p.ns_pid not in held or size > held[p.ns_pid][0]:
                    held[p.ns_pid] = (size, d.pdev)
    sized = [d for d in devices or () if getattr(d, "vram_total", None)]
    only = sized[0].pdev if len(sized) == 1 else None
    placed: dict[str, list[ModelRow]] = {}
    cpu: list[ModelRow] = []
    unplaced: list[ModelRow] = []
    for r in rows:
        card = held[r.pid][1] if r.pid is not None and r.pid in held else None
        if card is None and r.vram == 0:
            cpu.append(r)
            continue
        card = card or only
        if card is not None:
            placed.setdefault(card, []).append(r)
        else:
            unplaced.append(r)
    out: dict = {pdev: {"rows": rs, "asked": models.get("asked", []), "failed": models.get("failed", []),
                        "louise": sum(r.vram for r in rs if r.louise),
                        "other": sum(r.vram for r in rs if not r.louise)}
                 for pdev, rs in placed.items()}
    out["cpu"] = cpu
    out["unplaced"] = unplaced
    return out


def _get_json(url: str):
    """Split out so tests stub the network without patching httpx, which
    TestClient itself runs on (as app/gpu.py does)."""
    import httpx
    r = httpx.get(url, timeout=TIMEOUT_S)
    r.raise_for_status()
    return r.json()


def panel(collections, *, extra_urls=(), chat_model: str = "", translate_model: str = "",
          reading: dict[str, str] | None = None, speech: dict[str, str] | None = None,
          get_json=None) -> dict:
    """The breakdown: rows from every LocalAI that answers `/system`, the two
    sums, and the origins that did not answer (named, never silent)."""
    get_json = get_json or _get_json
    uses = louise_uses(collections, chat_model=chat_model, translate_model=translate_model,
                       reading=reading, speech=speech)
    rows: list[ModelRow] = []
    seen: set = set()
    asked, failed = [], []
    for o in origins(collections, extra_urls):
        try:
            system = get_json(o + "/system")
        except Exception as e:   # noqa: BLE001 — a panel line, never the page's failure
            failed.append(f"{o}: {type(e).__name__}")
            continue
        if not isinstance(system, dict) or "loaded_models" not in system:
            continue              # not a LocalAI
        asked.append(o)
        rows += rows_from(system, uses, seen)
    rows.sort(key=lambda r: (-r.vram, r.model))
    return {"rows": rows, "asked": asked, "failed": failed,
            "louise": sum(r.vram for r in rows if r.louise),
            "other": sum(r.vram for r in rows if not r.louise)}


def bar(models: dict, total: int | None, used: int | None) -> dict[str, float] | None:
    """Per cent of the card: Louise's models, others' models, held outside
    LocalAI (used minus every model), free."""
    if not total or used is None or not models.get("asked"):
        return None
    louise, other = models["louise"], models["other"]
    outside = max(0, used - louise - other)
    pct = lambda b: 100.0 * b / total      # noqa: E731
    return {"louise": pct(louise), "other": pct(other), "outside": pct(outside),
            "outside_bytes": outside, "free": max(0.0, 100.0 - pct(louise) - pct(other) - pct(outside))}
