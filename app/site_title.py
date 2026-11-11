"""The node's title — the label at the top left of every page, the browser
tab's name and the saved PDF's header (operator, 2026-11-11: "The label on
top left is 'Project Louise', customizable is the settings").

DESIRED STATE, IN THE NODE'S FILE (CLAUDE.md invariant 10): platform.toml
`[node] title`, "Project Louise" when unset. Every page reads it through
`title()`, so an edit shows at the next page load, no restart. Set by the
operator from the command line (`louise node title set …`; RULE 2: an
operator's choice lives there, not in a dialog).

`write` edits ONE line: `title = …` inside `[node]` replaced where it
stands, or added under the table's header, or a `[node]` table appended —
every other line of the file kept byte for byte, since `[node]` also holds
the node's name, address and ports. The result is read back as the node
reads it (`platform_config._load_one`) before it replaces the file, so a
file the node could not load is never left written. THE STANDARD
ALTERNATIVE is a style-preserving TOML editor (tomlkit), named in
app/voice.py's docstring for when several commands write tables; this one
writes a single key.
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path

from app import platform_config

DEFAULT = platform_config.DEFAULT_TITLE

_TABLE = re.compile(r"^\s*\[\s*node\s*\]\s*(#.*)?$")
_ANY_TABLE = re.compile(r"^\s*\[")
_KEY = re.compile(r"^\s*title\s*=")


def title() -> str:
    """The title every page and PDF shows: the file's, else the default.
    Never raises — a file that does not load shows the default, and the
    System page says why the file does not load."""
    try:
        return platform_config.load().title
    except Exception:   # noqa: BLE001 — a page is never refused for its label
        return DEFAULT


def rewrite(text: str, value: str | None) -> str:
    """platform.toml's text with `[node] title` set to `value`, or removed
    for None; every other line byte for byte."""
    lines = text.splitlines(keepends=True)
    line = f"title = {json.dumps(value, ensure_ascii=False)}\n" if value is not None else None
    start = next((i for i, ln in enumerate(lines) if _TABLE.match(ln.rstrip("\r\n"))), None)
    if start is None:
        if line is None:
            return text
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        sep = ["\n"] if lines and lines[-1].strip() else []
        return "".join(lines + sep + ["[node]\n", line])
    end = next((i for i in range(start + 1, len(lines)) if _ANY_TABLE.match(lines[i])), len(lines))
    at = next((i for i in range(start + 1, end) if _KEY.match(lines[i])), None)
    if at is not None:
        if line is None:
            del lines[at]
        else:
            lines[at] = line
    elif line is not None:
        lines.insert(start + 1, line)
    return "".join(lines)


def write(path: Path, value: str | None) -> dict:
    """`louise node title set|reset`: `[node] title` written into `path`
    (None or "" removes it: the default again). Read back as the node reads
    it before it takes the file's place. Raises ScheduleError, naming the
    file, for a title the node would refuse or a file it cannot load."""
    value = (value or "").strip() or None
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        before = platform_config._load_one(path).title if path.is_file() else DEFAULT
    except (tomllib.TOMLDecodeError, OSError) as e:
        raise platform_config.schedule_mod.ScheduleError(f"{path}: {e}") from None
    tmp = path.with_name(path.name + ".title.tmp")
    tmp.write_text(rewrite(text, value), encoding="utf-8")
    try:
        try:
            got = platform_config._load_one(tmp).title
        except tomllib.TOMLDecodeError as e:
            raise platform_config.schedule_mod.ScheduleError(
                f"{path}: with [node] title written the file would not load ({e}); nothing was changed"
            ) from None
        if got != (value or DEFAULT):
            raise platform_config.schedule_mod.ScheduleError(
                f"{path}: [node] title read back as {got!r}, not {value!r} — is [node] also written "
                f"another way (a dotted key, an inline table)? Nothing was changed")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return {"file": str(path), "title": got, "before": before, "changed": got != before}
