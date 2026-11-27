"""THE CARD CAP: the card seen as smaller than it is, for a test (operator,
2026-11-27: "For test purpose we can limit the b70 to 16 GB to pre optimize
the scenario"; docs/notes/perugia-16gb-test.md §0).

`[models] card_cap_mib` in platform.toml — desired state (Tier 2, CLAUDE.md
invariant 10): written by `louise node resident set --card-cap MIB` (07
§7.3; `POST /api/node/resident/set`), never by hand, never into the node's
database; unset by default, removed by an empty value.

APPLIED IN ONE PLACE: where Louise reads the card from the GPU probe
(`gpu.from_json`, through `gpu.capped`). Every room decision reads the card
there — the keeper's room check and budget (app/resident.py `_card`), a
load's eviction (app/model_evict.py), a question's room
(app/question_card.py), a gate's twin (app/gate_twin.py), and what is shown
(the System page through `gpu.panel`, app/gpu_models.py's bar, `node
resident show`). Each sees the card's total as min(its own, the cap) and the
driver's count of what is used as it is, so past the cap every one reads no
room (free 0 under the card, negative under the reclaimer's line) and the
keeper evicts, or the rule in force pages. The card's own total is kept
beside it (`GpuDevice.vram_total_real`) for display.

WHAT IT DOES NOT DO: it is Louise's view, not the card's. The model server's
own reclaimer (LocalAI's `memory_reclaimer_threshold`) and the engine's
allocator still see the whole card, so a load that bypasses Louise's keeper
is not stopped: it is reported, once per crossing, by the keeper's node event
(app/resident.py `_say_over_cap`). And the model sizes a capped B70 learns
are SYCL's, not another backend's.

THE STANDARD ALTERNATIVE (CLAUDE.md, "Do not reinvent the wheel"): a cap the
engine itself enforces — the shape of PyTorch's
`torch.cuda.set_per_process_memory_fraction`, of Ollama's
`OLLAMA_GPU_OVERHEAD` (memory its scheduler keeps back on each card), or a
hardware partition (NVIDIA's MIG). The node's model server offers none for
its runners on xe/SYCL; adopting one would take every runner reading a card
size, and it would pay once a load that bypasses the keeper should be
refused, not only reported.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

MIB = 1 << 20
KEY = "card_cap_mib"


def cap_mib() -> int | None:
    """`[models] card_cap_mib` as the node reads it — `platform_config.seen`,
    re-read only when the file changes, the last good reading where it does
    not load; None unset."""
    from app import platform_config
    try:
        return platform_config.seen().models.card_cap_mib
    except Exception:   # noqa: BLE001 — a file that cannot be read caps nothing
        return None


def cap_bytes() -> int | None:
    mib = cap_mib()
    return None if mib is None else int(mib) * MIB


def real_card(fetch=None) -> tuple[int, str] | None:
    """(the bytes of the card's own total, its name): of the cards with
    memory of their own, the largest, as its driver counts it — the probe's
    answer read UNCAPPED; None where no probe is set or it cannot say."""
    from app import config, gpu
    if not config.GPU_PROBE_URL:
        return None
    try:
        devices = gpu.from_json((fetch or gpu._get_json)(config.GPU_PROBE_URL.rstrip("/") + "/gpu"),
                                cap=None).devices
    except Exception:   # noqa: BLE001 — unread: said by the caller
        return None
    own = [d for d in devices if d.vram_total and d.integrated is not True]
    if not own:
        return None
    d = max(own, key=lambda d: d.vram_total)
    return int(d.vram_total), str(d.name or d.card)


def parse(text: str) -> int | None:
    """The command's value: an empty one removes the cap (None); otherwise
    a whole number of MiB, at least CARD_CAP_MIN_MIB. ValueError in words."""
    from app.platform_config import CARD_CAP_MIN_MIB
    text = str(text).strip().replace("_", "").replace(",", "")
    if not text:
        return None
    try:
        mib = int(text)
    except ValueError:
        raise ValueError(f"the card cap is a whole number of MiB (16384 for a 16 GB card), not {text!r}") from None
    if mib < CARD_CAP_MIN_MIB:
        raise ValueError(f"the card cap is at least {CARD_CAP_MIN_MIB:,} MiB, not {mib:,}: under it no "
                         f"model set this node serves would load, which tests nothing")
    return mib


def write(path: Path, text: str, fetch=None) -> dict[str, Any]:
    """`node resident set --card-cap`: the cap into platform.toml's `[models]`
    (`platform_config.set_keys`, every other line kept, the file read back as
    the node reads it before it replaces the old one), or removed for an
    empty value. Refused in words (ValueError) under the floor, or above the
    card's own total where the probe can say it; where it cannot, written,
    and said that it was not checked (a cap above the card caps nothing)."""
    from app import platform_config
    mib = parse(text)
    card = real_card(fetch)
    if mib is not None and card is not None and mib * MIB > card[0]:
        raise ValueError(f"the card cap of {mib:,} MiB is above the card's own {card[0] // MIB:,} MiB "
                         f"({card[1]}): a cap above the card would cap nothing")
    changed = platform_config.set_keys(path, "models", {KEY: mib})
    out: dict[str, Any] = {KEY: mib, "changed": changed, "file": str(path)}
    if card is not None:
        out |= {"card": card[1], "card_total_mib": card[0] // MIB}
    if mib is None:
        out["says"] = ("the cap is removed: every room decision sees the card's own total again, "
                       "from the worker's next beat")
    else:
        out["says"] = (f"every room decision sees the card as {mib:,} MiB"
                       + (f" of its {card[0] // MIB:,}" if card is not None else "")
                       + ", the driver's count of what is used unchanged, from the worker's next beat. "
                         "The model server's own reclaimer and the engine still see the whole card: a load "
                         "past the cap that bypasses the keeper is not stopped, it is reported once per "
                         "crossing in the node's events")
        if card is None:
            out["note"] = ("the card's own total could not be read (GPU_PROBE_URL unset or the probe "
                           "silent): the cap was not checked against it — a cap above the card caps nothing")
    return out
