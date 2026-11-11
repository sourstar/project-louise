"""The node's voice: which speech models the Ask page uses, and whether it
offers voice at all (operator, 2026-11-07: "voice support in Louise's web
GUI, after the node's speech models run on the GPU").

THE MODELS ARE THE NODE'S CHOICE, NEVER THE READER'S (CLAUDE.md RULE 2).
platform.toml's `[speech]` (Tier 2, invariant 10; 07 §7.8) names them on
the node's model server — CHAT_URL's, as app/speech.py has always used —
and `settings` is the ONE function every reader of them calls: the
transcription relay (app/transcribe.py), the speech relay (app/speech.py),
the Ask page, `louise node speech show` and the System page's GPU panel.

    [speech]
    stt_model = "whisper-large-v3"   # speech to text: POST /v1/audio/transcriptions
    vad_model = "silero-vad"         # voice activity: POST /v1/vad, a clip trimmed to its speech
    tts_model = "omnivoice-cpp"      # text to speech: POST /v1/audio/speech
    tts_voice = "localai://voice-profiles/1c834741"

THE VOICE IS THE READER'S CHOICE among the node's (operator, 2026-11-11:
"Add a configuration tab, there the user can choose philippe or
valentina"): `voices` lists the voice profiles the model server holds —
each a recorded person, by name — and the node's `tts_voice` as the one
used when the reader chose none. The model stays the node's.

A model unset is a voice the page does not offer: no button, no form, and
nothing asks. `offered` says why in the operator's words, with the command
that supplies it — for `node speech show` and `GET /ask/voice`; the page
only hides the button.

`write` is `louise node speech set`: the `[speech]` table replaced where it
stood, or appended, every other line kept byte for byte — as `share peers
add` writes `[peers]` (app/share/peers.py) — and the result read back as the
node reads it before it replaces the file. THE STANDARD ALTERNATIVE
(CLAUDE.md, "Do not reinvent the wheel"): a style-preserving TOML editor
(tomlkit) edits one table in place and keeps its comments; it is a new
dependency, and pays once a third command writes a table of its own — the
three would then share it.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from app import config, httpc, platform_config
from app.platform_config import SPEECH_KEYS, SpeechConfig

#: the command line's word for each key (`node speech set --stt …`)
FLAGS = {"stt_model": "--stt", "vad_model": "--vad", "tts_model": "--tts", "tts_voice": "--voice"}
#: `GET /v1/models` of the model server, for `show`: a typo is seen at once
LIST_TIMEOUT_S = 5.0
#: what the browser needs before it lets a page open the microphone
MICROPHONE_NOTE = ("the microphone is offered where the browser allows a page to open it: the node "
                   "reached over https, or at localhost on the node itself (every browser's rule for "
                   "getUserMedia); over plain http on the LAN the page shows no microphone")

_HEAD = re.compile(r"^\[speech\]\s*(#.*)?$")
#: what loading the platform file raises: its own check, a TOML that does
#: not parse, a file that cannot be read
LOAD_ERRORS = (platform_config.schedule_mod.ScheduleError, tomllib.TOMLDecodeError, OSError)


def server() -> str:
    """The model server the speech models run on: CHAT_URL's (07 §7.8)."""
    return config.CHAT_URL.rstrip("/")


def settings(cfg: platform_config.PlatformConfig | None = None) -> SpeechConfig:
    """THE resolution of the node's speech models. Raises one of
    LOAD_ERRORS when the platform file does not load."""
    return (cfg if cfg is not None else platform_config.load()).speech


def settings_or_none() -> SpeechConfig:
    """`settings`, or none set when the platform file does not load: a
    relay that names its own model still works (`offered` says why)."""
    try:
        return settings()
    except LOAD_ERRORS:
        return SpeechConfig()


def _offer(model: str | None, what: str, flag: str) -> dict[str, Any]:
    if model:
        return {"available": True, "why": None}
    return {"available": False,
            "why": f"no {what} model is set on this node: `louise node speech set {flag} <model>`"}


def offered() -> dict[str, Any]:
    """What the Ask page offers: voice `input` (the microphone) and `output`
    (the answer read aloud), each `available` or not and `why` not, in the
    operator's words. Never raises."""
    try:
        s = settings()
    except LOAD_ERRORS as e:
        why = f"{platform_config.platform_path()} does not load ({e})"
        return {"input": {"available": False, "why": why}, "output": {"available": False, "why": why}}
    return {"input": _offer(s.stt_model, "speech-to-text", FLAGS["stt_model"])
            | {"trimmed": bool(s.stt_model and s.vad_model)},
            "output": _offer(s.tts_model, "text-to-speech", FLAGS["tts_model"])}


def voices() -> dict[str, Any]:
    """The voices the Ask page offers the reader: `{"voices": [{name, voice,
    language}], "default": voice}` — the model server's voice profiles in
    the order they were recorded, and the one used when the reader chose
    none (the node's `tts_voice`, else the first recorded; app/speech.py
    `voice_for`). Nothing when the node reads no answer aloud. Never raises."""
    from app import speech
    offer = offered().get("output") or {}
    if not offer.get("available"):
        return {"voices": [], "default": None}
    try:
        node = settings()
    except LOAD_ERRORS:
        return {"voices": [], "default": None}
    found = sorted(speech.profiles(server()), key=lambda p: str(p.get("created_at") or ""))
    out = [{"name": str(p.get("name") or p.get("voice")), "voice": p["voice"],
            "language": p.get("language")} for p in found if p.get("voice")]
    return {"voices": out, "default": speech.voice_for(node.tts_voice or None, found)}


def models_listed(url: str, names: list[str]) -> dict[str, bool] | None:
    """Whether the model server lists each name (`GET /v1/models`); None
    when it cannot be asked now."""
    try:
        r = httpc.get(f"{url}/v1/models", timeout=LIST_TIMEOUT_S)
        if r.status_code != 200:
            return None
        ids = {str(m.get("id")) for m in (r.json() or {}).get("data") or [] if isinstance(m, dict)}
    except Exception:   # noqa: BLE001 — a line of `show`, never its failure
        return None
    return {n: n in ids for n in names}


def show() -> dict[str, Any]:
    """`louise node speech show`: the settings as the node resolves them,
    the file they come from, the server, what the page offers and whether
    the server lists each model."""
    s = settings()
    values = {k: getattr(s, k) for k in SPEECH_KEYS}
    models = sorted({v for k, v in values.items() if v and k != "tts_voice"})
    listed = models_listed(server(), models) if models else {}
    out: dict[str, Any] = {"file": str(platform_config.platform_path()), "server": server(),
                           "speech": values, "offered": offered(), "listed": listed,
                           "note": MICROPHONE_NOTE}
    if listed is None:
        out["listed_note"] = f"the model server at {server()} did not answer GET /v1/models"
    elif any(not ok for ok in listed.values()):
        out["listed_note"] = ("not listed by the model server: "
                              + ", ".join(sorted(n for n, ok in listed.items() if not ok)))
    return out


def _render(values: dict[str, str | None]) -> list[str]:
    body = [f"{k} = {json.dumps(values[k], ensure_ascii=False)}\n" for k in SPEECH_KEYS if values.get(k)]
    if not body:
        return []
    return ["[speech]      # the Ask page's voice, on the node's model server (07 §7.8); "
            "`louise node speech set` writes here\n"] + body


def rewrite(text: str, values: dict[str, str | None]) -> str:
    """platform.toml's text with its `[speech]` table replaced — where it
    stood, or at the end — and every other line kept byte for byte; no
    table at all when every value is unset. Comments inside the table do
    not survive."""
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    at: int | None = None
    i = 0
    while i < len(lines):
        if _HEAD.match(lines[i].rstrip("\r\n")):
            if at is None:
                at = len(kept)
            i += 1
            while i < len(lines) and not lines[i].startswith("["):
                i += 1
            continue
        kept.append(lines[i])
        i += 1
    fresh = _render(values)
    if at is None:
        if not fresh:
            return "".join(kept)
        if kept and not kept[-1].endswith("\n"):
            kept[-1] += "\n"
        if kept and kept[-1].strip():
            kept.append("\n")
        return "".join(kept + fresh)
    while at < len(kept) and not kept[at].strip():
        del kept[at]
    tail = kept[at:]
    if not fresh:                       # the table gone: and the blank line put before it
        head = kept[:at]
        while not tail and head and not head[-1].strip():
            head.pop()
        return "".join(head + tail)
    if tail and tail[0].strip():
        fresh = fresh + ["\n"]
    return "".join(kept[:at] + fresh + tail)


def write(path: Path, changes: dict[str, str]) -> dict[str, Any]:
    """`louise node speech set`: each key of `changes` set — or removed, for
    an empty value — in `path`'s `[speech]`. The file is read as the node
    reads it, written whole through a temporary file, and that file read
    back the same way before it takes the file's place: a value the node
    could not load is never left written. Raises ScheduleError, naming
    the file, for anything that stops it."""
    unknown = sorted(set(changes) - set(SPEECH_KEYS))
    if unknown:
        raise platform_config.schedule_mod.ScheduleError(f"[speech] takes no {', '.join(unknown)}")
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        cfg = platform_config._load_one(path) if path.is_file() else platform_config.PlatformConfig()
    except (tomllib.TOMLDecodeError, OSError) as e:
        raise platform_config.schedule_mod.ScheduleError(f"{path}: {e}") from None
    before = {k: getattr(cfg.speech, k) for k in SPEECH_KEYS}
    after = dict(before)
    for k, v in changes.items():
        after[k] = (v or "").strip() or None
    changed = [k for k in SPEECH_KEYS if after[k] != before[k]]
    if changed:
        tmp = path.with_name(path.name + ".speech.tmp")
        tmp.write_text(rewrite(text, after), encoding="utf-8")
        try:
            try:
                got = platform_config._load_one(tmp).speech
            except tomllib.TOMLDecodeError as e:
                raise platform_config.schedule_mod.ScheduleError(
                    f"{path}: with [speech] rewritten the file would not load ({e}) — is the table "
                    f"also written another way (a dotted key, an inline table)? Nothing was changed"
                ) from None
            if {k: getattr(got, k) for k in SPEECH_KEYS} != after:
                raise platform_config.schedule_mod.ScheduleError(
                    f"{path}: [speech] written does not read back as set; nothing was changed")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(path)
    return {"file": str(path), "speech": after, "changed": changed}
