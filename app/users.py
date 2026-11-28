"""The node's users: who may search (07 §7.4, amended 2026-10-22).

Operator, 2026-10-22: "a search-only gui (aka user gui). A user token giving
only access to the ask tab", with add, remove, role, expiry, reissue and
revoke. A user is a NAME the operator chose, a ROLE — `user` (Ask, the
reader, pictures, translation, Help, Connect, and the machine interfaces:
`/v1`, `/api/ask`, MCP) or `admin` (everything, as the admin token) — an
optional expiry, and a token.

Desired state, so a TOML file: `${CONFIG_DIR}/users.toml`, beside
platform.toml (invariant 10). It holds the SHA-256 of each token, never the
token (invariant 8): the token is printed once, by `add` and `reissue`, and
a lost one is reissued, never recovered. `louise user …` and the admin API
are its writers, under one lock; the web reads it again when it changes.

`last_seen` is not desired state: it is kept in `<LOUISE_ROOT>/state/
users-seen.json`, written at most once a minute per user.

THE ACCOUNT'S VOICE (operator, 2026-11-11: "Shall a user account can choose
it?"): `voice`, the voice profile the answer is read in for this user, is
the user's own desired state, so it lives in the same file — written by the
user from the Ask page's Settings (`POST /ask/voice`, `set_voice`) or by the
operator (`louise user voice`), under the same lock. It follows the account
to every browser and client; none set, the node's own (app/speech.py).

Until the first ACTIVE user exists, Ask stays open on the LAN as it always
was; from then on it needs a token (`auth.viewer`).
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import secrets
import threading
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator

ROLES = ("user", "admin")
PREFIX = "lu_"
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
SEEN_EVERY_S = 60.0


class UserError(ValueError):
    """A request the users file cannot honour; the message names why."""


@dataclass(frozen=True)
class User:
    name: str
    role: str
    token_sha256: str
    created: str                     # ISO 8601 UTC, seconds
    expires: str | None = None       # ISO 8601 UTC; None = never
    revoked: str | None = None       # when it was revoked; None = not
    voice: str | None = None         # the voice the answer is read in; None = the node's
    #: the phrases that cover the wait (docs/notes/hands-free-voice.md §7): False
    #: = turned off, the tone instead; None = not chosen, on by default
    phrases: bool | None = None
    #: the name hands-free is called by, the reader's own (operator,
    #: 2026-11-27: "the activation name can be customized"); None: the node's
    wake_name: str | None = None
    #: THE USERS TAB (operator, 2026-11-28): the person's first name and name,
    #: both optional; what this reader may not see — collections and
    #: annotations collections by `collection_id`, so a rename keeps the
    #: choice — and the voices it may be read to in ("every voice disabled by
    #: default: if there is no voice, the read aloud feature is disabled")
    first_name: str | None = None
    last_name: str | None = None
    hide_collections: tuple[str, ...] = ()
    hide_annotations: tuple[str, ...] = ()
    voices: tuple[str, ...] = ()

    def state(self, now: dt.datetime | None = None) -> str:
        if self.revoked:
            return "revoked"
        if self.expires and _parse_time(self.expires) <= (now or _now()):
            return "expired"
        return "active"

    def public(self, seen: dict[str, str] | None = None) -> dict:
        """What may be shown: everything but the token's hash."""
        return {"name": self.name, "role": self.role, "created": self.created, "expires": self.expires,
                "revoked": self.revoked, "state": self.state(), "voice": self.voice, "phrases": self.phrases, "wake_name": self.wake_name,
                "first_name": self.first_name, "last_name": self.last_name,
                "hide_collections": list(self.hide_collections), "hide_annotations": list(self.hide_annotations),
                "voices": list(self.voices),
                "last_seen": (seen or {}).get(self.name)}


@dataclass(frozen=True)
class Guest:
    """What a reader with NO account is shown and read to in (the users tab's
    first entry): the same lists as an account's, kept as `[guest]` in the
    same file. Nothing hidden and no voice by default."""
    hide_collections: tuple[str, ...] = ()
    hide_annotations: tuple[str, ...] = ()
    voices: tuple[str, ...] = ()

    def public(self) -> dict:
        return {"hide_collections": list(self.hide_collections),
                "hide_annotations": list(self.hide_annotations), "voices": list(self.voices)}


def path() -> Path:
    return Path(os.getenv("CONFIG_DIR", ".")) / "users.toml"


def _seen_path() -> Path:
    from app import config
    return config.louise_root() / "state" / "users-seen.json"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def parse_expiry(text: str | None, *, now: dt.datetime | None = None) -> str | None:
    """`never` | `<N>d` | `YYYY-MM-DD` (valid through that day, UTC) |
    an ISO 8601 time → the stored expiry, or None for never."""
    t = (text or "never").strip().lower()
    now = now or _now()
    if t in ("never", "none", ""):
        return None
    m = re.fullmatch(r"(\d{1,5})d", t)
    if m:
        return _iso(now + dt.timedelta(days=int(m.group(1))))
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
            day = dt.date.fromisoformat(t)
            return _iso(dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), dt.timezone.utc))
        return _iso(_parse_time(text.strip()))
    except ValueError:
        raise UserError(f"expiry {text!r}: `never`, `90d`, a date (2027-03-31) or an ISO time") from None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return PREFIX + secrets.token_urlsafe(32)


# ---- the file ---------------------------------------------------------------

def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)        # a TOML basic string is a JSON string


def _ids(v) -> str:
    return "[" + ", ".join(_q(x) for x in v) + "]"


def _dump(users: list[User], guest: "Guest | None" = None) -> bytes:
    out = ["# Louise users (07 §7.4). Written by `louise user …`, the admin API and a user's own voice;",
           "# a token is never stored, only its sha256. Edit with the CLI.", ""]
    if guest is not None and (guest.hide_collections or guest.hide_annotations or guest.voices):
        out.append("[guest]")
        for key in ("hide_collections", "hide_annotations", "voices"):
            if getattr(guest, key):
                out.append(f"{key} = {_ids(getattr(guest, key))}")
        out.append("")
    for u in sorted(users, key=lambda u: u.name):
        out.append("[[user]]")
        out.append(f"name = {_q(u.name)}")
        out.append(f"role = {_q(u.role)}")
        out.append(f"token_sha256 = {_q(u.token_sha256)}")
        out.append(f"created = {_q(u.created)}")
        if u.expires:
            out.append(f"expires = {_q(u.expires)}")
        if u.revoked:
            out.append(f"revoked = {_q(u.revoked)}")
        if u.voice:
            out.append(f"voice = {_q(u.voice)}")
        if u.phrases is not None:
            out.append(f"phrases = {'true' if u.phrases else 'false'}")
        if u.wake_name:
            out.append(f"wake_name = {_q(u.wake_name)}")
        if u.first_name:
            out.append(f"first_name = {_q(u.first_name)}")
        if u.last_name:
            out.append(f"last_name = {_q(u.last_name)}")
        for key in ("hide_collections", "hide_annotations", "voices"):
            if getattr(u, key):
                out.append(f"{key} = {_ids(getattr(u, key))}")
        out.append("")
    return "\n".join(out).encode("utf-8")


def _strings(row: dict, key: str) -> tuple[str, ...]:
    v = row.get(key) or []
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise UserError(f"{path()}: {key} is a list of ids, got {v!r}")
    return tuple(dict.fromkeys(v))


def _table(raw: bytes) -> dict:
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise UserError(f"{path()} does not read ({e}); nobody but the admin token gets in") from None


def _parse_guest(raw: bytes) -> Guest:
    row = _table(raw).get("guest") or {}
    return Guest(*(_strings(row, k) for k in ("hide_collections", "hide_annotations", "voices")))


def _parse(raw: bytes) -> list[User]:
    data = _table(raw)
    out = []
    for row in data.get("user") or []:
        role = str(row.get("role") or "user")
        if role not in ROLES:
            raise UserError(f"{path()}: user {row.get('name')!r} has role {role!r}; roles: {', '.join(ROLES)}")
        out.append(User(name=str(row["name"]), role=role, token_sha256=str(row["token_sha256"]),
                        created=str(row.get("created") or ""), expires=row.get("expires") or None,
                        revoked=row.get("revoked") or None, voice=str(row.get("voice") or "") or None,
                        phrases=row["phrases"] if isinstance(row.get("phrases"), bool) else None,
                        wake_name=str(row.get("wake_name") or "") or None,
                        first_name=str(row.get("first_name") or "") or None,
                        last_name=str(row.get("last_name") or "") or None,
                        hide_collections=_strings(row, "hide_collections"),
                        hide_annotations=_strings(row, "hide_annotations"),
                        voices=_strings(row, "voices")))
    return out


_cache: tuple[tuple, list[User], Guest] | None = None
_cache_lock = threading.Lock()


def _load_all() -> tuple[list[User], Guest]:
    global _cache
    p = path()
    try:
        st = p.stat()
    except FileNotFoundError:
        return [], Guest()
    stamp = (str(p), st.st_ino, st.st_size, st.st_mtime_ns)
    with _cache_lock:
        if _cache is not None and _cache[0] == stamp:
            return _cache[1], _cache[2]
    raw = p.read_bytes()
    users, guest = _parse(raw), _parse_guest(raw)
    with _cache_lock:
        _cache = (stamp, users, guest)
    return users, guest


def load() -> list[User]:
    """Every user in the file, read again only when the file changed."""
    return _load_all()[0]


def load_guest() -> Guest:
    """What a reader with no account may see and hear (`[guest]`)."""
    return _load_all()[1]


@contextmanager
def _editing() -> Iterator[list[User]]:
    """Read-modify-write under an exclusive lock beside the file; written
    to a sibling and renamed whole (invariant 2)."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_name(".users.toml.lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            raw = p.read_bytes() if p.exists() else b""
            users = _parse(raw) if raw else []
            guest = [_parse_guest(raw) if raw else Guest()]
            yield users if not _GUEST_EDIT.get() else (users, guest)
            tmp = p.with_name(".users.toml.tmp")
            tmp.write_bytes(_dump(users, guest[0]))
            os.chmod(tmp, 0o600)
            os.replace(tmp, p)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


import contextvars
#: `_editing` yields `(users, [guest])` instead of the users, for the guest's writer
_GUEST_EDIT: contextvars.ContextVar[bool] = contextvars.ContextVar("users_guest_edit", default=False)


def _index(users: list[User], name: str) -> int:
    for i, u in enumerate(users):
        if u.name == name:
            return i
    raise UserError(f"no user {name!r}")


def _role(role: str) -> str:
    if role not in ROLES:
        raise UserError(f"role {role!r}: one of {', '.join(ROLES)}")
    return role


# ---- the verbs (CLI and admin API alike) --------------------------------------

def add(name: str, *, role: str = "user", expires: str | None = None) -> tuple[User, str]:
    """A new user and its token — the only time the token exists in clear."""
    if not NAME.fullmatch(name or ""):
        raise UserError(f"name {name!r}: lowercase letters, digits, '.', '_' or '-', up to 64")
    token = _new_token()
    with _editing() as users:
        if any(u.name == name for u in users):
            raise UserError(f"user {name!r} exists; `louise user reissue {name}` gives a new token")
        user = User(name=name, role=_role(role), token_sha256=hash_token(token), created=_iso(_now()),
                    expires=parse_expiry(expires))
        users.append(user)
    return user, token


def set_role(name: str, role: str) -> User:
    from app import config
    with _editing() as users:
        i = _index(users, name)
        role = _role(role)
        if role != "admin" and users[i].role == "admin" and not config.ADMIN_TOKEN \
                and not any(u.role == "admin" and u.name != name and u.state() == "active" for u in users):
            # no admin token and no other admin: nobody could undo it
            raise UserError(f"{name} is the last admin and no admin token is set: make another admin first")
        users[i] = replace(users[i], role=role)
        return users[i]


def _clean(text: str | None, what: str) -> str | None:
    t = (text or "").strip()
    if len(t) > 120 or any(c in t for c in "\r\n\x00"):
        raise UserError(f"{what} is one line of at most 120 characters")
    return t or None


def _id_list(values, what: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str) or not all(isinstance(v, str) and v and len(v) <= 512 for v in values):
        raise UserError(f"{what} is a list of ids")
    return tuple(dict.fromkeys(values))


def set_profile(name: str, *, first_name=..., last_name=..., hide_collections=..., hide_annotations=...,
                voices=...) -> User:
    """The users tab's values of one account; a value left out is kept."""
    with _editing() as users:
        i = _index(users, name)
        u = users[i]
        changes = {}
        if first_name is not ...:
            changes["first_name"] = _clean(first_name, "first name")
        if last_name is not ...:
            changes["last_name"] = _clean(last_name, "name")
        for key, v in (("hide_collections", hide_collections), ("hide_annotations", hide_annotations),
                       ("voices", voices)):
            if v is not ...:
                changes[key] = _id_list(v, key)
        if "voices" in changes and u.voice and u.voice not in changes["voices"]:
            changes["voice"] = None             # a chosen voice no longer allowed: the default again
        users[i] = replace(u, **changes)
        return users[i]


def set_guest(*, hide_collections=..., hide_annotations=..., voices=...) -> Guest:
    """The values for readers with no account (`[guest]`); a value left out is kept."""
    token = _GUEST_EDIT.set(True)
    try:
        with _editing() as (_users, guest):
            g = guest[0]
            changes = {k: _id_list(v, k) for k, v in (("hide_collections", hide_collections),
                                                      ("hide_annotations", hide_annotations),
                                                      ("voices", voices)) if v is not ...}
            guest[0] = replace(g, **changes)
            return guest[0]
    finally:
        _GUEST_EDIT.reset(token)


def _lists_of(viewer):
    """The account or guest lists that apply to `viewer`; None for one who
    sees all (an admin, the operator: app/viewing.py)."""
    from app import viewing
    if viewing.sees_all(viewer):
        return None
    try:
        users_, guest = _load_all()
    except UserError:
        return Guest()          # a users file that does not read: nothing hidden for a reason we cannot read, no voice
    if viewer is not None and getattr(viewer, "role", None) == "user":
        u = next((u for u in users_ if u.name == viewer.name), None)
        return u if u is not None else Guest()
    if viewer is not None and getattr(viewer, "role", None) == "deposit":
        return Guest()          # the depositor (LocalAGI): no list of its own, no voice
    return guest                # a guest, and nobody


def hidden_ids(viewer) -> frozenset[str]:
    """The collection ids (collections and annotations) hidden for `viewer` by
    the users tab; empty for one who sees all."""
    got = _lists_of(viewer)
    return frozenset() if got is None else frozenset((*got.hide_collections, *got.hide_annotations))


def voices_allowed(viewer) -> frozenset[str] | None:
    """The voices `viewer` may be read to in (none by default: no voice, no
    read-aloud); None for one who may use every voice (an admin)."""
    got = _lists_of(viewer)
    return None if got is None else frozenset(got.voices)


def set_expiry(name: str, expires: str | None) -> User:
    with _editing() as users:
        i = _index(users, name)
        users[i] = replace(users[i], expires=parse_expiry(expires))
        return users[i]


def reissue(name: str) -> tuple[User, str]:
    """A new token; the old one stops working at once. A revoked user is
    active again; role and expiry are kept."""
    token = _new_token()
    with _editing() as users:
        i = _index(users, name)
        users[i] = replace(users[i], token_sha256=hash_token(token), revoked=None)
        return users[i], token


def revoke(name: str) -> User:
    with _editing() as users:
        i = _index(users, name)
        if not users[i].revoked:
            users[i] = replace(users[i], revoked=_iso(_now()))
        return users[i]


def set_voice(name: str, voice: str | None) -> User:
    """The voice the answer is read in for this user; None: the node's."""
    voice = (voice or "").strip() or None
    if voice is not None and (len(voice) > 512 or any(c in voice for c in "\r\n\x00")):
        raise UserError("a voice is a voice profile's id, on one line")
    with _editing() as users:
        i = _index(users, name)
        users[i] = replace(users[i], voice=voice)
        return users[i]


def set_phrases(name: str, on: bool | None) -> User:
    """Whether this user hears the phrases that cover the wait; None: the default (on)."""
    if on is not None and not isinstance(on, bool):
        raise UserError("phrases is on or off")
    with _editing() as users:
        i = _index(users, name)
        users[i] = replace(users[i], phrases=on)
        return users[i]


def set_wake_name(name: str, wake: str | None) -> User:
    """The name this user calls hands-free by; None or "": the node's."""
    from app import voice_wake
    wake = voice_wake.valid_name(wake) if wake else None
    with _editing() as users:
        i = _index(users, name)
        users[i] = replace(users[i], wake_name=wake)
        return users[i]


def wake_name_of(name: str | None) -> str | None:
    """The account's own hands-free name, None for no account or none chosen."""
    if not name:
        return None
    try:
        return next((u.wake_name for u in load() if u.name == name), None)
    except UserError:
        return None


def phrases_of(name: str | None) -> bool | None:
    """The account's choice of the phrases, None for no account or none made;
    a users file that does not read gives None, never an error."""
    if not name:
        return None
    try:
        return next((u.phrases for u in load() if u.name == name), None)
    except UserError:
        return None


def voice_of(name: str | None) -> str | None:
    """The account's voice, None for no account or none chosen; a users
    file that does not read gives None (the node's voice), never an error."""
    if not name:
        return None
    try:
        return next((u.voice for u in load() if u.name == name), None)
    except UserError:
        return None


def remove(name: str) -> None:
    with _editing() as users:
        del users[_index(users, name)]


# ---- who is asking --------------------------------------------------------------

def any_active() -> bool:
    now = _now()
    return any(u.state(now) == "active" for u in load())


def resolve(token: str | None) -> User | None:
    """The ACTIVE user this token belongs to, or None."""
    if not token or not token.startswith(PREFIX):
        return None
    want = hash_token(token)
    now = _now()
    for u in load():
        if secrets.compare_digest(u.token_sha256, want):
            return u if u.state(now) == "active" else None
    return None


_seen: dict[str, float] = {}
_seen_lock = threading.Lock()


def touch(name: str, *, now: float | None = None) -> None:
    """Note the user was seen; written at most once a minute per user, and
    never an error to the request that caused it."""
    now = time.time() if now is None else now
    with _seen_lock:
        if now - _seen.get(name, 0.0) < SEEN_EVERY_S:
            return
        _seen[name] = now
    try:
        p = _seen_path()
        try:
            seen = json.loads(p.read_bytes())
        except (OSError, ValueError):
            seen = {}
        seen[name] = _iso(dt.datetime.fromtimestamp(now, dt.timezone.utc))
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".users-seen.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(seen, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def seen() -> dict[str, str]:
    try:
        return dict(json.loads(_seen_path().read_bytes()))
    except (OSError, ValueError):
        return {}


def listing() -> list[dict]:
    s = seen()
    return [u.public(s) for u in sorted(load(), key=lambda u: u.name)]
