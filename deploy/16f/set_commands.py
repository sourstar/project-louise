"""deploy/16f/set_commands.py — what the louise-rag app's web and worker RUN,
taken from the tree's deploy/truenas-app.yaml into the live app's config.

The deploy edits the live `custom_compose_config` in place (the mounts, the
memory caps, the image) and never applies deploy/truenas-app.yaml itself, so
a change of a service's `command` in the file reached no node: #779's worker
command, `python -m app.worker_guard` (the OOM hold, operator 2026-11-19),
would have left the worker running unguarded, the file saying otherwise.
München found it at the deploy of 8c56161 (2026-11-20) and set it by hand
once. Since then the file is the one place a command is changed, and the
deploy carries it.

Applied by set_platform_image.py in ITS `app.update` (`apply` here, called
there): the image and the command it runs arrive together, never an image
started on the command of the one before — the guard's module does not
exist in an older image. A service the file gives no command runs its
image's own CMD; a live command the file does not state is said, and left.

Standardised alternative, not taken: applying the whole file (TrueNAS's
`app.update` with deploy/truenas-app.yaml as `custom_compose_config`), as
step 8 does for the GPU governor. It would make the file the whole truth,
but the live config holds what the file does not (the node's mounts, the
build and cache areas flatten_mounts.py sets per host, the secrets' paths),
so the file would first have to state all of it per node. It would pay once
the node's config is generated rather than edited.
"""
from __future__ import annotations

from pathlib import Path

APP = "louise-rag"
SERVICES = ("web", "worker")
FILE = Path(__file__).resolve().parents[1] / "truenas-app.yaml"


def wanted(path: Path = FILE) -> dict[str, list[str] | None]:
    """Each service's `command` as the file states it; None: the image's CMD."""
    import yaml
    services = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("services") or {}
    out: dict[str, list[str] | None] = {}
    for name in SERVICES:
        cmd = (services.get(name) or {}).get("command")
        if cmd is not None and not (isinstance(cmd, list) and cmd and all(isinstance(x, str) for x in cmd)):
            raise ValueError(f"{path.name}: {name}'s command is not a list of words: {cmd!r}")
        out[name] = cmd
    return out


def apply(cfg: dict, want: dict[str, list[str] | None]) -> tuple[list[str], list[str]]:
    """Set each command the file states, in place. Returns (changes, notes):
    one line per command changed, and one per live command the file does
    not state (left as it is). Refuses a config it does not recognise
    before any change."""
    services = cfg.get("services")
    if not isinstance(services, dict):
        raise ValueError("the app config has no services mapping; not touched")  # noqa: TRY004 — the caller's one refusal
    for name in want:
        if not isinstance(services.get(name), dict):
            raise ValueError(f"the app config has no {name} service; not touched")  # noqa: TRY004
    changes: list[str] = []
    notes: list[str] = []
    for name, cmd in want.items():
        have = services[name].get("command")
        if cmd is None:
            if have is not None:
                notes.append(f"{name:7} command {have} is not in {FILE.name}: left")
            continue
        if have != cmd:
            changes.append(f"{name:7} command {have} -> {cmd}")
            services[name]["command"] = list(cmd)
    return changes, notes


def missing(after: dict, want: dict[str, list[str] | None]) -> list[str]:
    """The services whose live command is not the one the file states."""
    services = after.get("services") or {}
    return [name for name, cmd in want.items()
            if cmd is not None and (services.get(name) or {}).get("command") != cmd]
