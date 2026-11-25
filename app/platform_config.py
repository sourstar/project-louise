"""Tier-2 node configuration — `${CONFIG_DIR:-.}/platform.toml`.

architecture-v2.md §14 puts bandwidth policy in Tier 2 (versioned files:
diffable, backupable, "a node restores from git clone + compose up"), NOT in
env (Tier 1 is identity/infrastructure) and NOT in the DB (CLAUDE.md invariant
10: never write desired state into the DB). This module is that reader.

Conventions deliberately mirror app/collections.py, the repo's only other TOML
reader: stdlib tomllib, open(..., "rb"), ${CONFIG_DIR:-.} rooting, absent file
-> hard-coded defaults rather than an exception, no caching.

The surface is §5.2's, verbatim:

    [bandwidth]  down_max="400Mbit"  up_max="40Mbit"  reserve_interactive="20%"
                 schedule="01:00-07:00 full; else 30%"
    [transfer]   workers=8  per_endpoint=4  segments_per_file=1

(§14's example omits reserve_interactive/[transfer] and nests schedule under
[bandwidth]; §5.2 is the more complete of the two and the task specifies it, so
it wins. schedule is read from [bandwidth] either way, so both spellings load.)

With no platform.toml present, bandwidth is UNLIMITED and the schedule is
always-full — an existing deployment upgrades with zero behavior change, the
same philosophy collections.py follows for a missing collections/ directory.
"""
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .transfer import schedule as schedule_mod

DEFAULT_WORKERS = 8
#: `[node] title` unset: the label every page and saved PDF shows (app/site_title.py)
DEFAULT_TITLE = "Project Louise"
#: a title is one line of at most this many characters
TITLE_MAX = 80
DEFAULT_PER_ENDPOINT = 4
DEFAULT_RESERVE_INTERACTIVE = 0.20   # §5.2: "a reserved interactive headroom (~20%)"
# Hard ceiling on connections opened for ONE file. Not a performance limit —
# an etiquette one. Origins see per-client connection counts, not intentions.
MAX_SEGMENTS_PER_FILE = 8


@dataclass(frozen=True)
class BandwidthConfig:
    down_max_bps: int | None = None          # None = unlimited
    up_max_bps: int | None = None
    reserve_interactive: float = DEFAULT_RESERVE_INTERACTIVE
    schedule: schedule_mod.Schedule = schedule_mod.ALWAYS_FULL
    schedule_text: str = ""                  # kept verbatim for the UI ("schedule overlaid")


@dataclass(frozen=True)
class TransferConfig:
    workers: int = DEFAULT_WORKERS
    per_endpoint: int = DEFAULT_PER_ENDPOINT
    segments_per_file: int = 1


@dataclass(frozen=True)
class ShareConfig:
    """`[share]` (07 §7.8). `patch_cache_s`: how long this peer keeps a part
    patch it computed for a peer (app/share/patch.py), so the many peers
    asking for the same patch after a new snapshot get it without a
    recompute; 0 deletes each patch once served (operator, 2026-10-12)."""
    patch_cache_s: int = 7 * 86400


@dataclass(frozen=True)
class TranslateConfig:
    """`[translate]` (07 §7.8). `reading`: {source language: model} — the
    Ask page's translation of what is READ (results, the reader) for a text
    in that language goes to that model instead of TRANSLATE_MODEL
    (app/translate.py `reading_model`). Searches never use it: a query is
    translated by TRANSLATE_MODEL. Node-wide, in no Codex (operator,
    2026-10-12; docs/notes/translator-choice.md: Index-Translate-9B for
    Latin and Ancient Greek)."""
    reading: dict[str, str] = field(default_factory=dict)
    #: MADLAD's slots (`parallel:N` of TRANSLATE_MODEL's file on the model
    #: server), applied by app/translate_slots.py; None leaves the file alone
    slots: int | None = None


@dataclass(frozen=True)
class TrustConfig:
    """architecture-v2.md §14's `[trust]` block, the part P1.2 needs.

    `space_authors` are the pubkeys whose SIGNED space artifacts are fully
    trusted. This is deliberately Tier-2 file config and deliberately NOT
    trust-on-first-use: an artifact defines what "honest vectors" means, so
    trusting one on sight would hand an attacker the definition. An artifact
    from anyone else still loads — as ADVISORY, allowed to tighten checks but
    never to loosen a tolerance below the built-in coarse floor.
    """
    space_authors: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdmissionConfig:
    """`[admission]` — 08 §8.2's aggregate limit (07 §7.8).

    What THIS peer accepts per hour from keys whose work it has not seen
    reproduced. "Nothing in the protocol reads another peer's setting,
    and no peer can tell what another's is: the limit governs what *this*
    peer accepts, so unlike a threshold that had to be agreed it cannot
    drift, cannot be negotiated, and cannot be gamed by anyone but its
    owner." Zero on either allowance accepts nothing from strangers,
    which §8.2 calls a complete configuration and not a broken one.
    """
    bytes_per_hour: int = 64 * 1024 ** 2
    objects_per_hour: int = 1000
    queue_bytes: int = 256 * 1024 ** 2


@dataclass(frozen=True)
class CertifierConfig:
    """`[certifier]` (07 §7.8; 09 §9.11): what this node says of ITS OWN
    key when it serves the anchor document at
    `/.well-known/louise-certifier.json` — from when the key certifies,
    and when it was revoked, if ever. Both 09 §9.1 times. Only a node
    that fronts itself with TLS on a domain the operator controls is an
    anchor (03 §3.7); the document is served regardless."""
    valid_from: str | None = None
    revoked_at: str | None = None


@dataclass(frozen=True)
class WebConfig:
    """`[web]` (07 §7.8, added 2026-11-22). `idle_trim_s`: after this many
    seconds without a question, the web gives back the heap it freed, once
    (app/idle_trim.py; operator, 2026-11-22: the web under 1 GB, "after 15
    minutes without a question", configurable); 0 never."""
    idle_trim_s: int = 900


@dataclass(frozen=True)
class AskConfig:
    """`[ask]` (07 §7.8): the reranker's latency bound (05 §5.6). A list
    the reranker cannot order inside it keeps its fused order and says
    `reranked: false`. 8000 by the operator's decision (2026-09-15): at
    the spec's former 400 every list fell back, and the GPU reranker takes
    0.3–0.7 s on pubmed and wikipedia and 3–6 s on gutenberg's long chunks.

    `translate_share` (added 2026-10-10, 05 §5.2): a translated query goes
    out only into a collection's MAIN languages — its text index groups,
    largest first, until they hold this share of its documents. 0.90 is
    gutenberg's en, fr, fi, de (92 % of the books); measured offline,
    translating into those alone beat all nineteen (descriptive MRR 0.231
    → 0.245, R@50 0.605 → 0.630; docs/notes/one-language-per-work.md).
    1.0 is every language with an index of its own, as before."""
    latency_budget_ms: int = 8000
    translate_share: float = 0.90


@dataclass(frozen=True)
class ModelsConfig:
    """`[models]` (07 §7.8, added 2026-10-09): `load_timeout_s` bounds the
    LOAD of a model a runner reports as not loaded — the warm request
    app/model_load.py sends before the real one, so that no request's
    budget holds a cold start (operator, 2026-10-09). A model measured on
    the node has its own bound (app/model_loads.json, from
    docs/notes/model-profile.md); this is the bound of every other. 120 s:
    the slowest load measured is LocalAGI's 2B chat model at 44–46 s
    (docs/notes/vega.md §11), and a load can be slowed by the card's other
    work.

    `keep_ask_resident` and `preload_speech` (added 2026-11-08, a TRIAL:
    operator, option A, "keep every model Ask uses resident on the card";
    "The load all is a test"; "It applies to any collection, on demand or
    all embedding preloads the model. Chat, speech, rerankers, madlad are
    preloaded too"): the worker preloads the models the question path uses
    (app/resident.py) — the chat, translation and reranking models and the
    encoders of every collection whose embed is on-demand, all or p2p under
    the first, the `[speech]` models under the second — preloaded, not
    pinned: the reclaimer evicts the least-recently-used model when space
    is claimed (operator 2026-11-08). Both default true; false, that part
    is not preloaded. Nothing is unloaded for being idle either way."""
    load_timeout_s: int = 120
    keep_ask_resident: bool = True
    preload_speech: bool = True
    #: `chat` and `translate` (added 2026-11-15, operator: "I want them to be
    #: editable from the page for quick testing"): the chat and translation
    #: models, OVER the environment's CHAT_MODEL and TRANSLATE_MODEL, which stay
    #: the defaults. Desired state in the file, so the System page and the
    #: command line write the same thing; unset, the environment's
    #: (app/config.py resolves both on every read).
    chat: str | None = None
    translate: str | None = None
    #: `chat_large` and `chat_small` (added 2026-11-25, operator: "The current
    #: chat model setting becomes 2 chat settings, large one and a small one"):
    #: the chat model of CHAT mode and of EMBED mode (app/node_mode.py). Each
    #: unset reads `chat`, the one setting there was — so a file that names
    #: only `chat` answers with it in both modes, as before — and then the
    #: environment's CHAT_MODEL.
    chat_large: str | None = None
    chat_small: str | None = None
    #: HOW A LOAD THAT DOES NOT FIT THE CARD GETS ITS ROOM (added 2026-11-24,
    #: operator: "Extend page instead of unload to the dense models").
    #: "unload", the rule since 2026-11-16: the model with the oldest real use
    #: is unloaded. "page", THE DEFAULT (operator, 2026-11-25: "Set the new
    #: paging mode as the default in the engine"; where the card's driver was
    #: measured to page — app/model_evict.py PAGING_DRIVERS — and unload under
    #: any other): for a load WORK is waiting on, nothing is unloaded
    #: while the host can hold what the card cannot — the driver moves idle
    #: models' memory to host memory and back at their next use (measured on
    #: the B70: 0.2–0.4 s more on a small model's next request, against
    #: 7.5–8.7 s after an unload) — and the unload stays the fallback
    #: (app/model_evict.py). The keeper's own preloads keep "unload" either way.
    make_room: str = "page"
    #: in "page", the host memory that must stay AVAILABLE once the overflow is
    #: paged, in GiB: under it a load unloads as before. A default to be
    #: replaced by the node's own measurement of its busy hours — the host's
    #: OOM killer is what stands under it (2026-11-24: 4.7 GB available under
    #: load, where nothing may be paged)
    page_floor_gib: int = 12
    #: in "page", the most the card may be committed OVER its size once the
    #: load is in, in GiB — the loaded models at their largest plus the load,
    #: less the card. 3: what was measured holds 3.5 (S5 part 2: a model whose
    #: context is made on demand loaded and answered there, a 1.9 GiB
    #: context); further over, nobody has seen a context's reservation
    #: succeed, and a refused one kills its backend. Raised only by a
    #: measurement
    page_over_gib: int = 3


#: `[models] make_room`
MAKE_ROOM = ("unload", "page")


#: the run modes CHAT mode may switch the node to (operator, 2026-11-25: "a
#: run mode among download/seeding, seeding or pause"), in the page's order;
#: the first is the one a file that names none gets
CHAT_RUNS = ("acquire-only", "seed-only", "paused")
#: A MODE'S NETWORK CHOICE (operator, 2026-11-25: "In both embed and query
#: modes you can choose to download and seed, seed only, no network"; and of
#: where it is kept: "set for each embed/query mode, a cron job then simply
#: switches that"): each of the two modes has its own, and a switch of mode
#: brings it. The first is what a file that names none gets
NETWORKS = ("download-seed", "seed", "none")
NETWORK_LABELS = (("download-seed", "download and seed"), ("seed", "seed only"), ("none", "no network"))
#: `chat_run`, the morning's key (query mode's run mode), read as query
#: mode's network choice: the three it could be are the three networks
CHAT_RUN_AS_NETWORK = {"acquire-only": "download-seed", "seed-only": "seed", "paused": "none"}
#: EMBED MODE'S EMBEDDING CHOICE (operator, 2026-11-25: "In embed mode you can
#: also choose local/p2p embedding"; "Network and embedding mode remain
#: personal choices"): who embeds the backlog of the collections that embed
#: one — each as its own file says (the first, what a file that names none
#: gets: nothing changes), every part here (`embed = "all"`, "all local"), or
#: the parts rendezvous gives this node, published as it goes (`embed =
#: "p2p"`, "all p2p"). In force in embed mode only, over the collections
#: whose file says all or p2p (`collections.desired_embed_mode`)
EMBEDDINGS = ("collection", "local", "p2p")
EMBEDDING_LABELS = (("collection", "each collection's own"), ("local", "all local"), ("p2p", "all p2p"))
#: `[modes]`'s rate keys: each mode's network budget. Query mode's keep the
#: morning's name (`chat_*`)
MODES_RATE_KEYS = ("embed_down_max", "embed_up_max", "chat_down_max", "chat_up_max")
#: `[modes]`'s keys: each mode's network choice, embed mode's embedding
#: choice, and the budgets
MODES_KEYS = ("embed_network", "query_network", "chat_run", "embed_embedding") + MODES_RATE_KEYS


@dataclass(frozen=True)
class ModesConfig:
    """`[modes]` (07 §7.8, added 2026-11-25): what the node's two modes of
    use — EMBED and CHAT, app/node_mode.py — set when the node is switched
    to one. `chat_run` is the run mode chat mode switches to (embed mode
    switches to `running`, "run all"). `<mode>_down_max` / `<mode>_up_max`
    are that mode's network budget, in `[budget]`'s grammar, standing in
    `[budget] down_max` / `up_max`'s place while the node is in the mode
    (the schedule and the reserve stay `[budget]`'s); unset, `[budget]`'s.
    The texts are kept as written, for the page. WHICH mode the node is in
    is not here: it is the node's state, set by a command, the page or the
    API, and it survives a restart (invariant 10's exception)."""
    embed_network: str = NETWORKS[0]
    query_network: str = NETWORKS[0]
    embed_embedding: str = EMBEDDINGS[0]
    embed_down_max_bps: int | None = None
    embed_up_max_bps: int | None = None
    chat_down_max_bps: int | None = None
    chat_up_max_bps: int | None = None
    text: tuple[tuple[str, str], ...] = ()      # (key, as written) of the keys the file sets

    def budget(self, mode: str) -> tuple[int | None, int | None]:
        """(down, up) of a mode, bytes a second; None where it sets none."""
        if mode in ("query", "chat"):          # query mode's keys keep the morning's name
            return self.chat_down_max_bps, self.chat_up_max_bps
        if mode == "embed":
            return self.embed_down_max_bps, self.embed_up_max_bps
        return None, None                       # custom: [budget]'s, as before there were modes

    def network(self, mode: str) -> str | None:
        """The network choice of a mode; None for a mode that has none
        (custom: its run mode says what the network does, as before)."""
        if mode in ("query", "chat"):
            return self.query_network
        if mode == "embed":
            return self.embed_network
        return None

    def embedding(self, mode: str) -> str | None:
        """Embed mode's embedding choice (`EMBEDDINGS`); None in a mode that
        has none — query embeds nothing, custom leaves every collection its
        own file's word, as before there were modes."""
        return self.embed_embedding if mode == "embed" else None

    def written(self, key: str) -> str:
        return dict(self.text).get(key, "")


@dataclass(frozen=True)
class SpeechConfig:
    """`[speech]` (07 §7.8, added 2026-11-07): the models the Ask page's
    voice uses on the node's model server (CHAT_URL's) — speech to text,
    voice activity, text to speech and its voice. THE NODE'S CHOICE, never
    a reader's (CLAUDE.md RULE 2): `louise node speech set` writes them,
    and a model unset is a voice the page does not offer (app/voice.py)."""
    stt_model: str | None = None
    vad_model: str | None = None
    tts_model: str | None = None
    #: the voice an ADMIN hears when none is chosen (a recorded profile)
    tts_voice: str | None = None
    #: the voices every other reader hears, one per language the answer is in
    #: — synthetic voices, never a recorded person's: "it=<voice>, en=<voice>"
    #: (`synthetic_voices`, app/voice.py `synthetic`)
    synthetic_voices: str | None = None
    #: the steps of one synthesis ("24"): `params.flow_steps` of every request
    #: to the text-to-speech model — fewer are faster and coarser. Unset, the
    #: model's own default (OmniVoice: 32). The node's choice, never a
    #: reader's (operator, 2026-11-25: "add the steps to the admin settings")
    tts_steps: str | None = None


SPEECH_KEYS = ("stt_model", "vad_model", "tts_model", "tts_voice", "synthetic_voices", "tts_steps")
#: `[speech] tts_steps`: the range a synthesis is asked for (the model's
#: default is 32, and 16 and 32 are what was measured; the bounds only refuse a typo)
TTS_STEPS = (4, 64)


def tts_steps(text: str | None) -> int | None:
    """`[speech] tts_steps` read: the steps of one synthesis, or None where
    unset. Raises ScheduleError for a value that is not a whole number in
    TTS_STEPS."""
    if text is None or not str(text).strip():
        return None
    t = str(text).strip()
    if not t.isdecimal() or not TTS_STEPS[0] <= int(t) <= TTS_STEPS[1]:
        raise schedule_mod.ScheduleError(
            f"speech.tts_steps: {t!r} is not a whole number of steps from {TTS_STEPS[0]} to "
            f"{TTS_STEPS[1]} (the model's own default is 32)")
    return int(t)


def synthetic_voices(text: str | None) -> dict[str, str]:
    """`[speech] synthetic_voices` read: each language's voice, in the order
    written (the first is said for a language that has none). Raises
    ScheduleError, naming the entry, for one that is not `language=voice`.
    A language is its primary tag, lower case ("it-IT" is "it")."""
    out: dict[str, str] = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        lang, sep, v = part.partition("=")
        lang = lang.strip().lower().replace("_", "-").split("-")[0]
        if not sep or not v.strip() or not lang.isalpha() or not 2 <= len(lang) <= 3:
            raise schedule_mod.ScheduleError(
                f"speech.synthetic_voices: {part.strip()!r} is not language=voice "
                f"(\"it=<voice>, en=<voice>\", a language by its ISO code)")
        if lang in out:
            raise schedule_mod.ScheduleError(f"speech.synthetic_voices: {lang!r} is given twice")
        out[lang] = v.strip()
    return out


@dataclass(frozen=True)
class DepositConfig:
    """`[deposit]` (07 §7.8; 02 §2.9): what a deposit collection is created
    with when the creator names nothing — a client's create carries no
    model. `encoder` is the endpoint; `space` a file holding the space
    block for it, needed once, by the first deposit collection on that
    encoder (later ones copy its space); `space_from` a Codex to copy the
    space from instead. `[deposit.collections.<name>] space_from` does the
    same for one name."""
    encoder: str | None = None
    space: Path | None = None
    space_from: str | None = None
    by_name: dict[str, str] = field(default_factory=dict)
    # 07 §7.8 (amended 2026-10-09): the rest of the `[encoder]` block a new
    # deposit collection's file is written with. `encoder` is the SERVER;
    # on LocalAI (api = "vega") one address serves every model, so the URL
    # names none and the model keys must. Absent, the model is the one a
    # model-container URL names (`…/upstream/<model>`), else create refuses.
    api: str = "openai"
    embed_model: str | None = None
    query_model: str | None = None


@dataclass(frozen=True)
class PeersConfig:
    """`[peers]`, `[dht]` and the node's own address (07 §7.8; 04 §4.2):
    the bootstrap addresses `share peers add` writes, the keys it pins to
    an address (`[peers.pins]`: a digest from that address under another
    key is discarded), whether the DHT is on, and where THIS node answers
    pulls — `[node] address`, the reachable one
    (`http://192.168.178.10:30042` on the reference node), not the
    container's port; `[node] name` labels it."""
    addresses: tuple[str, ...] = ()
    dht: bool = True
    address: str | None = None
    name: str | None = None
    pins: dict[str, str] = field(default_factory=dict)
    peer_port: int | None = None      # `[node] peer_port`: the swarm's port, as peers reach it


_KEY_TEXT = re.compile(r"ed25519:[0-9a-f]{64}")


def normalise_address(text: Any) -> str:
    """A peer address as the messages carry it (09 §9.9): `http(s)://host[:port]`,
    no path, no trailing slash. A bare `host:port` is http. Refused when it
    names no host or carries a path — one spelling per peer, so the list and
    the pins hold each address once."""
    from urllib.parse import urlsplit
    if not isinstance(text, str) or not text.strip():
        raise schedule_mod.ScheduleError(f"a peer address is http(s)://host[:port], got {text!r}")
    s = text.strip()
    if "://" not in s:
        s = "http://" + s
    parts = urlsplit(s)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if parts.scheme not in ("http", "https") or parts.path.strip("/") or parts.query \
            or parts.fragment or port == -1 \
            or not re.fullmatch(r"([A-Za-z0-9.\-]+|\[[0-9A-Fa-f:.]+\])(:\d{1,5})?", parts.netloc):
        raise schedule_mod.ScheduleError(f"a peer address is http(s)://host[:port], got {text!r}")
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(frozen=True)
class PlatformConfig:
    bandwidth: BandwidthConfig = BandwidthConfig()
    transfer: TransferConfig = TransferConfig()
    share: ShareConfig = ShareConfig()
    translate: TranslateConfig = TranslateConfig()
    trust: TrustConfig = TrustConfig()
    admission: AdmissionConfig = AdmissionConfig()
    ask: AskConfig = AskConfig()
    web: WebConfig = WebConfig()
    models: ModelsConfig = ModelsConfig()
    modes: ModesConfig = ModesConfig()
    speech: SpeechConfig = SpeechConfig()
    certifier: CertifierConfig = CertifierConfig()
    deposit: DepositConfig = DepositConfig()
    peers: PeersConfig = PeersConfig()
    # [node] title — the label at the top left of every page, the browser tab's
    # name and a saved PDF's header (operator, 2026-11-11); app/site_title.py
    title: str = DEFAULT_TITLE
    # [encoder_defaults.<plugin>] -> the raw [encoder]-shaped table. Kept
    # RAW here on purpose: app/collections.py owns EmbedRecipe and parses
    # it, and importing that module from this one would close an import
    # cycle (collections -> platform_config -> collections).
    #
    # The SPELLING is `encoder_defaults` (07 §7.8), matching the
    # `[encoder]` block a collection file carries. This code shipped it as
    # `embed_defaults`, which is the older word and reads as the MODE key
    # (`embed = "on-demand"`) sitting beside it. Both are read; the spec
    # name wins where a file declares both, and the alias is deprecated.
    embed_defaults: dict[str, dict] = field(default_factory=dict)
    # [node] codex_only — OBSOLETE since 2026-10-08: a node serves only Codex
    # collections, always (the pre-Codex shape is gone). Still accepted, so
    # a platform.toml that sets it loads; read by nothing.
    # Was: serve ONLY collections whose file names a Codex.
    # A node mid-migration holds both shapes; this is how an operator says
    # "a collection appears in Louise once it is converted, and not
    # before". Default False so nothing changes for a node that predates
    # the migration. Read by app/collections.py:codex_only().
    codex_only: bool = False
    # [node] quarantine — "report" or "enforce" (03 §3.6; migration plan, step
    # 14). A copy's state is always computed and shown; only "enforce"
    # WITHHOLDS a copy in quarantine. Default "report", and that is the first
    # of two deploys: the day the states exist no Codex collection on a node
    # has its run statement or its own origin check yet, so enforcing at once
    # would take every one of them out of search together. The operator
    # switches it once `hold collection show` reads in-use for each collection
    # meant to be. Removed at step 12. Read by app/copy_state.py:enforced().
    quarantine: str = "report"
    # [node] quarantine_in_use_before — the copies this peer ALREADY USED the day
    # it began to enforce (operator, 2026-09-20: "assume that the current
    # collections have already passed it; the next collection to be created
    # will be our guinea pig"). By COLLECTION ID, never a name: a name can be
    # given to another collection. It stands in for the two tests a copy made
    # before the tests existed cannot run yet — roots-and-chunks, the origin —
    # and for nothing else: never the licence, never the gate, never a test
    # that FAILED. Read by app/copy_state.py:in_use_before().
    quarantine_in_use_before: tuple[str, ...] = ()
    # [schedule] / [profiles] / [gpu.levels] — the node's quiet, day and night
    # profiles (app/node_profile.py); None when the file declares no schedule
    profiles: Any = None
    source_path: Path | None = None          # None = defaults (no file on disk)


_COLLECTION_ID = re.compile(r"sha256:[0-9a-f]{64}")


def _config_dir() -> Path:
    return Path(os.getenv("CONFIG_DIR", "."))


def platform_path() -> Path:
    return _config_dir() / "platform.toml"


def toml_literal(value: Any) -> str:
    """A TOML value as written in the file: a string (JSON's escapes are
    TOML's basic string's), or a {str: str} inline table."""
    import json
    if isinstance(value, dict):
        if not value:
            return "{}"
        return "{ " + ", ".join(f"{k} = {json.dumps(str(v), ensure_ascii=False)}"
                                for k, v in value.items()) + " }"
    return json.dumps(str(value), ensure_ascii=False)


def set_keys(path: Path, table: str, values: dict[str, Any]) -> list[str]:
    """Each `key = value` of `values` set in `path`'s `[table]` — its line
    replaced where it stood, added at the table's end, or the table added at
    the file's end — and a None value's line removed. Every other line kept
    byte for byte, comments included. The text is read back as the node
    reads it before it takes the file's place: a file that would not load is
    never written (ScheduleError, nothing changed). Returns the keys whose
    line changed. For one-line keys only: a key written as a dotted key or
    over several lines is refused by the read-back.

    THE STANDARD ALTERNATIVE (CLAUDE.md, "Do not reinvent the wheel"; as
    app/voice.py `write` says): tomlkit edits a table in place keeping its
    comments. This is the THIRD writer of platform.toml (with `[speech]` and
    `[peers]`), where voice.py said tomlkit would pay — adopting it is a new
    dependency (ask first); this keeps to one-line keys, which is all the
    System page writes."""
    path = Path(path)
    head = re.compile(rf"^\[{re.escape(table)}\]\s*(#.*)?$")
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError as e:
        raise schedule_mod.ScheduleError(f"{path}: {e}") from None
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if head.match(ln.rstrip("\r\n"))), None)
    changed: list[str] = []
    if start is None:
        add = [f"{k} = {toml_literal(v)}\n" for k, v in values.items() if v is not None]
        if not add:
            return []
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines += (["\n"] if lines and lines[-1].strip() else []) + [f"[{table}]\n"] + add
        changed = [k for k, v in values.items() if v is not None]
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
        for k, v in values.items():
            key = re.compile(rf"^\s*{re.escape(k)}\s*=")
            at = next((i for i in range(start + 1, end) if key.match(lines[i])), None)
            new = None if v is None else f"{k} = {toml_literal(v)}\n"
            if at is not None:
                if new is None:
                    del lines[at]
                    end -= 1
                    changed.append(k)
                elif lines[at] != new:
                    lines[at] = new
                    changed.append(k)
            elif new is not None:
                ins = end
                while ins > start + 1 and not lines[ins - 1].strip():
                    ins -= 1                    # before the blank lines that end the table
                lines.insert(ins, new)
                end += 1
                changed.append(k)
    if not changed:
        return []
    tmp = path.with_name(path.name + f".{table}.tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    try:
        try:
            got = tomllib.loads(tmp.read_text(encoding="utf-8")).get(table, {})
            _load_one(tmp)
        except tomllib.TOMLDecodeError as e:
            raise schedule_mod.ScheduleError(
                f"{path}: with [{table}] rewritten the file would not load ({e}); nothing was changed"
            ) from None
        for k, v in values.items():
            if got.get(k) != v:
                raise schedule_mod.ScheduleError(
                    f"{path}: [{table}] {k} written does not read back as set; nothing was changed")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)
    return changed


def _parse_fraction(value: Any, key: str) -> float:
    """'20%' | 0.2 | 20 -> 0.2. A bare number > 1 is read as a percentage,
    since '20' plainly means 20% and 0.2 plainly means 20% — both spellings
    appear in the wild and neither reading is ever ambiguous in range."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return DEFAULT_RESERVE_INTERACTIVE
        if text.endswith("%"):
            try:
                pct = float(text[:-1])
            except ValueError:
                raise schedule_mod.ScheduleError(
                    f"{key}: cannot parse percentage {value!r}") from None
        else:
            try:
                pct = float(text)
            except ValueError:
                raise schedule_mod.ScheduleError(
                    f"{key}: cannot parse {value!r} (try '20%')") from None
            if pct <= 1.0:
                pct *= 100.0
    else:
        pct = float(value)
        if pct <= 1.0:
            pct *= 100.0
    if not 0.0 <= pct < 100.0:
        raise schedule_mod.ScheduleError(
            f"{key}: {value!r} out of range — expected 0% <= x < 100%")
    return pct / 100.0


_SIZE_UNITS = {"": 1, "b": 1, "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3,
               "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3}


def _parse_bytes(value: Any, key: str, default: int) -> int:
    """`"64MB"` | `67108864` | `0` -> bytes. `0` is meaningful (08 §8.2:
    a peer that accepts nothing from strangers), so it is not a falsy
    stand-in for "unset" — absence is."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        n = int(value)
    else:
        text = str(value).strip().lower()
        i = 0
        while i < len(text) and (text[i].isdigit() or text[i] in "._"):
            i += 1
        unit = text[i:].strip()
        if unit not in _SIZE_UNITS:
            raise schedule_mod.ScheduleError(
                f"{key}: unknown size unit in {value!r} — use B, KB, MB, GB "
                f"(powers of 1000) or KiB, MiB, GiB (powers of 1024)")
        try:
            n = int(float(text[:i].replace("_", "")) * _SIZE_UNITS[unit])
        except ValueError:
            raise schedule_mod.ScheduleError(
                f"{key}: cannot parse a size from {value!r}") from None
    if n < 0:
        raise schedule_mod.ScheduleError(f"{key}: must be >= 0, got {n}")
    return n


def _positive_int(raw: dict, key: str, default: int) -> int:
    value = raw.get(key, default)
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise schedule_mod.ScheduleError(f"{key}: expected an integer, got {value!r}") from None
    if n < 1:
        raise schedule_mod.ScheduleError(f"{key}: must be >= 1, got {n}")
    return n


def _load_one(path: Path) -> PlatformConfig:
    with open(path, "rb") as f:
        raw: dict[str, Any] = tomllib.load(f)
    # 07 §7.8 names the table `[budget]`; `[bandwidth]` is this module's
    # older spelling and still loads. Only the older one was read, so a file
    # written as the spec says had its ceilings silently ignored (2026-10-19)
    if raw.get("budget") is not None and raw.get("bandwidth") is not None:
        raise schedule_mod.ScheduleError("[budget] and [bandwidth] are one table (07 §7.8): keep [budget]")
    bw_raw = raw.get("budget", raw.get("bandwidth", {})) or {}
    tr_raw = raw.get("transfer", {}) or {}
    trust_raw = raw.get("trust", {}) or {}
    share_raw = raw.get("share", {}) or {}
    cache_s = share_raw.get("patch_cache_s", ShareConfig.patch_cache_s)
    if not isinstance(cache_s, int) or isinstance(cache_s, bool) or cache_s < 0:
        raise schedule_mod.ScheduleError(
            f"[share] patch_cache_s is a number of seconds, 0 or more (0 = delete each "
            f"patch once served), got {cache_s!r}")
    reading_raw = (raw.get("translate", {}) or {}).get("reading", {}) or {}
    if not isinstance(reading_raw, dict) or not all(
            isinstance(k, str) and re.fullmatch(r"[a-z]{2,3}", k) and isinstance(v, str) and v.strip()
            for k, v in reading_raw.items()):
        raise schedule_mod.ScheduleError(
            "[translate] reading is {source language code: model name}, "
            "e.g. { la = \"index-translate-9b\" } (07 §7.8)")

    slots_raw = (raw.get("translate", {}) or {}).get("slots")
    if slots_raw is not None and (not isinstance(slots_raw, int) or isinstance(slots_raw, bool)
                                  or not 1 <= slots_raw <= 32):
        raise schedule_mod.ScheduleError(
            f"[translate] slots is how many segments the translation server decodes at once, "
            f"1 to 32, got {slots_raw!r} (07 §7.8)")

    authors_raw = trust_raw.get("space_authors", [])
    if not isinstance(authors_raw, list):
        raise schedule_mod.ScheduleError("trust.space_authors must be a list of pubkeys")
    authors = []
    for value in authors_raw:
        if not isinstance(value, str) or len(value) != 64:
            raise schedule_mod.ScheduleError(
                f"trust.space_authors entries must be 64-hex pubkeys, got {value!r}")
        try:
            bytes.fromhex(value)
        except ValueError:
            raise schedule_mod.ScheduleError(
                f"trust.space_authors entry is not hex: {value!r}") from None
        authors.append(value.lower())

    schedule_text = (bw_raw.get("schedule") or "").strip()
    bandwidth = BandwidthConfig(
        down_max_bps=schedule_mod.parse_rate(bw_raw.get("down_max")),
        up_max_bps=schedule_mod.parse_rate(bw_raw.get("up_max")),
        reserve_interactive=(
            _parse_fraction(bw_raw["reserve_interactive"], "reserve_interactive")
            if "reserve_interactive" in bw_raw else DEFAULT_RESERVE_INTERACTIVE),
        schedule=schedule_mod.parse_schedule(schedule_text),
        schedule_text=schedule_text,
    )

    segments = _positive_int(tr_raw, "segments_per_file", 1)
    if segments > MAX_SEGMENTS_PER_FILE:
        # §5.2 calls multi-segment downloads "default-off against origins".
        # They stay opt-in, and the ceiling is etiquette: past a handful of
        # connections one client stops being a polite peer and starts being
        # a small denial of service, with no throughput to show for it.
        raise schedule_mod.ScheduleError(
            f"segments_per_file={segments} exceeds the {MAX_SEGMENTS_PER_FILE} "
            f"connection ceiling for a single file (§5.2: default-off against "
            f"origins). Raise per_endpoint if you want more parallelism ACROSS "
            f"files — that is the axis origins mind less.")
    transfer = TransferConfig(
        workers=_positive_int(tr_raw, "workers", DEFAULT_WORKERS),
        per_endpoint=_positive_int(tr_raw, "per_endpoint", DEFAULT_PER_ENDPOINT),
        segments_per_file=segments,
    )
    # [encoder_defaults.<plugin>] — validated as "a table per plugin" only;
    # the field-level parse belongs to collections.parse_recipe, which owns
    # EmbedRecipe and reports errors in the same words for both files.
    # `embed_defaults` is the deprecated alias: read, merged UNDER the spec
    # name, and named by its own word in any error so the operator is told
    # which line of which file is wrong.
    embed_raw: dict = {}
    for key in ("embed_defaults", "encoder_defaults"):
        table_raw = raw.get(key, {}) or {}
        if not isinstance(table_raw, dict):
            raise schedule_mod.ScheduleError(
                f"{key} must be a table of per-plugin [encoder] blocks, "
                f"e.g. [{key}.pubmed]")
        for plugin, table in table_raw.items():
            if not isinstance(table, dict):
                raise schedule_mod.ScheduleError(
                    f"{key}.{plugin} must be a table, got {type(table).__name__}")
        embed_raw.update({str(k): dict(v) for k, v in table_raw.items()})

    adm_raw = raw.get("admission", {}) or {}
    if not isinstance(adm_raw, dict):
        raise schedule_mod.ScheduleError("admission must be a table")
    defaults = AdmissionConfig()
    objects = adm_raw.get("objects_per_hour", defaults.objects_per_hour)
    try:
        objects = int(objects)
    except (TypeError, ValueError):
        raise schedule_mod.ScheduleError(
            f"admission.objects_per_hour: expected an integer, got {objects!r}"
        ) from None
    if objects < 0:
        raise schedule_mod.ScheduleError(
            f"admission.objects_per_hour: must be >= 0, got {objects}")
    admission = AdmissionConfig(
        bytes_per_hour=_parse_bytes(adm_raw.get("bytes_per_hour"),
                                    "admission.bytes_per_hour",
                                    defaults.bytes_per_hour),
        objects_per_hour=objects,
        queue_bytes=_parse_bytes(adm_raw.get("queue"), "admission.queue",
                                 defaults.queue_bytes),
    )

    ask_raw = raw.get("ask", {}) or {}
    if not isinstance(ask_raw, dict):
        raise schedule_mod.ScheduleError("ask must be a table")
    share = ask_raw.get("translate_share", AskConfig().translate_share)
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 1:
        raise schedule_mod.ScheduleError(
            f"ask.translate_share: a share of documents, 0 < share <= 1, got {share!r} (07 §7.8)")
    ask = AskConfig(latency_budget_ms=_positive_int(
        ask_raw, "latency_budget_ms", AskConfig().latency_budget_ms),
        translate_share=float(share))

    web_raw = raw.get("web", {}) or {}
    if not isinstance(web_raw, dict):
        raise schedule_mod.ScheduleError("web must be a table (07 §7.8)")
    idle_trim_s = web_raw.get("idle_trim_s", WebConfig().idle_trim_s)
    if isinstance(idle_trim_s, bool) or not isinstance(idle_trim_s, int) or idle_trim_s < 0:
        raise schedule_mod.ScheduleError(
            f"web.idle_trim_s: seconds without a question before the web gives back its freed "
            f"memory, 0 or more (0 = never), got {idle_trim_s!r} (07 §7.8)")
    web_cfg = WebConfig(idle_trim_s=idle_trim_s)

    models_raw = raw.get("models", {}) or {}
    if not isinstance(models_raw, dict):
        raise schedule_mod.ScheduleError("models must be a table (07 §7.8)")
    for key in ("keep_ask_resident", "preload_speech"):
        if not isinstance(models_raw.get(key, True), bool):
            raise schedule_mod.ScheduleError(
                f"models.{key} is true or false, got {models_raw[key]!r} (07 §7.8)")
    for key in ("chat", "translate", "chat_large", "chat_small"):
        if models_raw.get(key) is not None and not isinstance(models_raw[key], str):
            raise schedule_mod.ScheduleError(
                f"models.{key} is a model name, got {models_raw[key]!r} (07 §7.8)")
    make_room = models_raw.get("make_room", ModelsConfig().make_room)
    if make_room not in MAKE_ROOM:
        raise schedule_mod.ScheduleError(
            f"models.make_room is \"unload\" or \"page\", got {make_room!r} (07 §7.8)")
    models = ModelsConfig(load_timeout_s=_positive_int(
        models_raw, "load_timeout_s", ModelsConfig().load_timeout_s),
        keep_ask_resident=models_raw.get("keep_ask_resident", True),
        preload_speech=models_raw.get("preload_speech", True),
        chat=(models_raw.get("chat") or "").strip() or None,
        translate=(models_raw.get("translate") or "").strip() or None,
        make_room=make_room,
        page_floor_gib=_positive_int(models_raw, "page_floor_gib", ModelsConfig().page_floor_gib),
        page_over_gib=_positive_int(models_raw, "page_over_gib", ModelsConfig().page_over_gib),
        chat_large=(models_raw.get("chat_large") or "").strip() or None,
        chat_small=(models_raw.get("chat_small") or "").strip() or None)
    modes = _modes(raw.get("modes", {}) or {})

    speech = _speech(raw.get("speech", {}) or {})

    cert_raw = raw.get("certifier", {}) or {}
    if not isinstance(cert_raw, dict):
        raise schedule_mod.ScheduleError("certifier must be a table (07 §7.8)")
    for key in ("valid_from", "revoked_at"):
        v = cert_raw.get(key)
        if v is not None and not (isinstance(v, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", v)):
            raise schedule_mod.ScheduleError(f"[certifier] {key} is a time YYYY-MM-DDTHH:MM:SSZ (09 §9.1)")
    certifier = CertifierConfig(valid_from=cert_raw.get("valid_from"), revoked_at=cert_raw.get("revoked_at"))

    node_raw = raw.get("node", {}) or {}
    title_raw = node_raw.get("title", DEFAULT_TITLE)
    if not (isinstance(title_raw, str) and title_raw.strip() and len(title_raw) <= TITLE_MAX
            and not any(ord(c) < 0x20 or ord(c) == 0x7f for c in title_raw)):
        raise schedule_mod.ScheduleError(
            f"[node] title is one line of 1-{TITLE_MAX} characters, the label every page shows "
            f"(`louise node title set`)")
    codex_only_raw = node_raw.get("codex_only", False)
    if not isinstance(codex_only_raw, bool):
        # Refused rather than coerced: `codex_only = "false"` read as truthy
        # would hide every unconverted collection on the node, which looks
        # exactly like data loss to whoever opens the Library.
        raise schedule_mod.ScheduleError(
            f"node.codex_only must be true or false, got {codex_only_raw!r}")
    quarantine_raw = node_raw.get("quarantine", "report")
    if quarantine_raw not in ("report", "enforce"):
        # refused, not coerced: a typo must not decide whether copies are
        # withheld — in either direction
        raise schedule_mod.ScheduleError(
            f"node.quarantine must be \"report\" or \"enforce\", got {quarantine_raw!r}")
    before_raw = node_raw.get("quarantine_in_use_before", [])
    if not isinstance(before_raw, list) or not all(
            isinstance(c, str) and _COLLECTION_ID.fullmatch(c) for c in before_raw):
        # refused, not skipped: an entry that names nothing would leave the copy
        # it was meant for withheld, and the operator reading "in the list"
        raise schedule_mod.ScheduleError(
            "node.quarantine_in_use_before must be a list of collection ids, each "
            f"\"sha256:\" and 64 hex digits (`louise hold collection show`), got {before_raw!r}")

    deposit = _deposit(raw.get("deposit") or {}, path)
    peers_raw = raw.get("peers") or {}
    addresses_raw = peers_raw.get("addresses", []) if isinstance(peers_raw, dict) else None
    if not isinstance(addresses_raw, list) or not all(isinstance(a, str) and a for a in addresses_raw):
        raise schedule_mod.ScheduleError("[peers] addresses is a list of strings (07 §7.8)")
    addresses: list[str] = []
    for a in addresses_raw:
        a = normalise_address(a)
        if a not in addresses:
            addresses.append(a)
    pins_raw = peers_raw.get("pins", {}) if isinstance(peers_raw, dict) else {}
    if not isinstance(pins_raw, dict) or not all(
            isinstance(k, str) and _KEY_TEXT.fullmatch(k) for k in pins_raw.values()):
        raise schedule_mod.ScheduleError(
            "[peers.pins] maps an address to the key it is pinned to, \"ed25519:\" and 64 hex "
            "(07 §7.8; 04 §4.2)")
    pins = {normalise_address(a): k for a, k in pins_raw.items()}
    dht_raw = raw.get("dht") or {}
    dht_enabled = dht_raw.get("enabled", True) if isinstance(dht_raw, dict) else True
    if not isinstance(dht_enabled, bool):
        raise schedule_mod.ScheduleError("[dht] enabled is true or false (07 §7.8)")
    node_address = node_raw.get("address")
    if node_address is not None and not (isinstance(node_address, str) and node_address.startswith("http")):
        raise schedule_mod.ScheduleError("[node] address is this node's reachable URL, http(s)://host:port")
    peer_port = node_raw.get("peer_port")
    if peer_port is not None and (not isinstance(peer_port, int) or isinstance(peer_port, bool)
                                  or not 1 <= peer_port <= 65535):
        raise schedule_mod.ScheduleError("[node] peer_port is the port this node's swarm listens "
                                         "on as peers reach it, 1-65535 (07 §7.8)")
    peers = PeersConfig(addresses=tuple(addresses), dht=dht_enabled, address=node_address,
                        name=node_raw.get("name") if isinstance(node_raw.get("name"), str) else None,
                        pins=pins, peer_port=peer_port)
    return PlatformConfig(bandwidth=bandwidth, transfer=transfer,
                          share=ShareConfig(patch_cache_s=cache_s),
                          translate=TranslateConfig(reading={k: v.strip() for k, v in reading_raw.items()},
                                                    slots=slots_raw),
                          deposit=deposit,
                          peers=peers,
                          trust=TrustConfig(space_authors=tuple(authors)),
                          admission=admission,
                          ask=ask,
                          web=web_cfg,
                          models=models,
                          modes=modes,
                          speech=speech,
                          title=title_raw.strip(),
                          certifier=certifier,
                          embed_defaults=embed_raw,
                          codex_only=codex_only_raw,
                          quarantine=quarantine_raw,
                          quarantine_in_use_before=tuple(before_raw),
                          profiles=_profiles(raw),
                          source_path=path)


def _modes(raw: Any) -> ModesConfig:
    """`[modes]`, checked at load: a key this does not know is refused (a
    misspelt budget would otherwise read as "no budget")."""
    if not isinstance(raw, dict):
        raise schedule_mod.ScheduleError("modes must be a table (07 §7.8)")
    unknown = sorted(set(raw) - set(MODES_KEYS))
    if unknown:
        raise schedule_mod.ScheduleError(
            f"[modes] has no key {', '.join(unknown)}: its keys are {', '.join(MODES_KEYS)} (07 §7.8)")
    networks: dict[str, str] = {}
    for key in ("embed_network", "query_network"):
        value = raw.get(key, NETWORKS[0])
        if value not in NETWORKS:
            raise schedule_mod.ScheduleError(
                f"[modes] {key} is that mode's network choice, one of {', '.join(NETWORKS)}, "
                f"got {value!r} (07 §7.8)")
        networks[key] = value
    if "chat_run" in raw:
        # the morning's key: query mode's run mode WAS its network choice
        if "query_network" in raw:
            raise schedule_mod.ScheduleError(
                "[modes] names both chat_run and query_network: chat_run is the first name of "
                "query_network — keep one (07 §7.8)")
        run = raw["chat_run"]
        if run not in CHAT_RUNS:
            raise schedule_mod.ScheduleError(
                f"[modes] chat_run (the first name of query_network) is one of "
                f"{', '.join(CHAT_RUNS)}, got {run!r} (07 §7.8)")
        networks["query_network"] = CHAT_RUN_AS_NETWORK[run]
    embedding = raw.get("embed_embedding", EMBEDDINGS[0])
    if embedding not in EMBEDDINGS:
        raise schedule_mod.ScheduleError(
            f"[modes] embed_embedding is embed mode's embedding choice, one of "
            f"{', '.join(EMBEDDINGS)}, got {embedding!r} (07 §7.8)")
    rates: dict[str, int | None] = {}
    for key in MODES_RATE_KEYS:
        value = raw.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int, float))):
            raise schedule_mod.ScheduleError(
                f"[modes] {key} is a rate as [budget]'s (\"50MB\", \"400Mbit\"), got {value!r} (07 §7.8)")
        try:
            rates[key] = schedule_mod.parse_rate(value)
        except schedule_mod.ScheduleError as e:
            raise schedule_mod.ScheduleError(f"[modes] {key}: {e}") from None
    return ModesConfig(
        embed_network=networks["embed_network"], query_network=networks["query_network"],
        embed_embedding=embedding,
        embed_down_max_bps=rates["embed_down_max"], embed_up_max_bps=rates["embed_up_max"],
        chat_down_max_bps=rates["chat_down_max"], chat_up_max_bps=rates["chat_up_max"],
        text=tuple((k, str(raw[k]).strip()) for k in MODES_KEYS if raw.get(k) is not None))


def _profiles(raw: dict[str, Any]):
    """[schedule] / [profiles] / [gpu.levels], parsed and checked at load so a
    typo is refused at boot, not at the next quiet period."""
    from . import node_profile
    return node_profile.parse(raw)


def _speech(raw: Any) -> SpeechConfig:
    """`[speech]`, checked at load: a misspelt key would leave the page
    without the voice the operator set, and nobody told why."""
    if not isinstance(raw, dict):
        raise schedule_mod.ScheduleError("[speech] is a table (07 §7.8)")
    unknown = sorted(set(raw) - set(SPEECH_KEYS))
    if unknown:
        raise schedule_mod.ScheduleError(
            f"[speech]: unknown key(s) {', '.join(unknown)}; it takes {', '.join(SPEECH_KEYS)} "
            f"(`louise node speech set` writes them)")
    for k, v in raw.items():
        if not isinstance(v, str) or not v.strip():
            raise schedule_mod.ScheduleError(
                f"speech.{k} is a model's (or a voice's) name, a non-empty string, got {v!r}")
    synthetic_voices(raw.get("synthetic_voices"))       # refused at load, not at a reading
    tts_steps(raw.get("tts_steps"))
    return SpeechConfig(**{k: raw[k].strip() for k in raw})


def _deposit(raw: Any, path: Path) -> DepositConfig:
    if not isinstance(raw, dict):
        raise schedule_mod.ScheduleError("[deposit] is a table (07 §7.8)")
    known = {"encoder", "api", "embed_model", "query_model", "space", "space_from",
             "collections"}
    unknown = sorted(set(raw) - known)
    if unknown:
        # refused, not ignored: a misspelt key would silently leave every
        # client-created collection on another encoder than the one meant
        raise schedule_mod.ScheduleError(
            f"[deposit]: unknown key(s) {', '.join(unknown)}; it takes "
            f"encoder, api, embed_model, query_model, space, space_from and "
            f"[deposit.collections.<name>]")
    for k in ("encoder", "api", "embed_model", "query_model", "space", "space_from"):
        if k in raw and not isinstance(raw[k], str):
            raise schedule_mod.ScheduleError(f"deposit.{k} is a string, got {raw[k]!r}")
    api, embed_model, query_model = _deposit_encoder(raw)
    by_name = {}
    for name, table in (raw.get("collections") or {}).items():
        if not isinstance(table, dict) or set(table) != {"space_from"} \
                or not isinstance(table["space_from"], str):
            raise schedule_mod.ScheduleError(
                f"[deposit.collections.{name}] carries space_from and nothing else")
        by_name[name] = table["space_from"]
    space = raw.get("space")
    return DepositConfig(
        encoder=raw.get("encoder") or None,
        space=((path.parent / space) if space else None),
        space_from=raw.get("space_from") or None,
        by_name=by_name,
        api=api, embed_model=embed_model, query_model=query_model)


def _deposit_encoder(raw: dict) -> tuple[str, str | None, str | None]:
    """`[deposit]`'s api and model keys (07 §7.8, amended 2026-10-09), checked
    as a collection file's `[encoder]` would check them — at load, so a
    wrong value is the operator's to see at once, not a client's create
    refused later, or worse a file written that cannot embed."""
    # one set of wire formats for every encoder block (app/hold/calibrate.py)
    from app.collections import model_named_by
    from app.hold.calibrate import APIS
    api = raw.get("api") or "openai"
    if api not in APIS:
        raise schedule_mod.ScheduleError(
            f"deposit.api is one of {', '.join(APIS)} (07 §7.8), got {api!r}")
    embed_model = raw.get("embed_model") or None
    query_model = raw.get("query_model") or None
    encoder = raw.get("encoder") or None
    if (embed_model or query_model or "api" in raw) and not encoder:
        raise schedule_mod.ScheduleError(
            "[deposit]: api, embed_model and query_model describe deposit.encoder, "
            "which is not set")
    if api == "vega" and not (embed_model and query_model):
        # LocalAI serves every model at one address: the URL names none, and
        # without the keys the file would be written with no model to ask for
        raise schedule_mod.ScheduleError(
            "[deposit]: api = \"vega\" requires embed_model and query_model — "
            "LocalAI's one address names no model, so the keys must (07 §7.8)")
    named = model_named_by(encoder) if encoder else None
    for key, value in (("embed_model", embed_model), ("query_model", query_model)):
        if named is not None and value is not None and value != named:
            raise schedule_mod.ScheduleError(
                f"deposit.{key} = {value!r} and deposit.encoder {encoder} names "
                f"{named!r}; on the model container the URL says which model, and "
                f"the key must agree with it (07 §7.8)")
    return api, embed_model, query_model


def load() -> PlatformConfig:
    """Re-reads on every call, exactly like collections.load_all() — config is
    small, and not caching means an operator edit needs no process restart to
    be visible in the System page's effective-config view."""
    path = platform_path()
    if not path.is_file():
        return PlatformConfig()
    return _load_one(path)


_SEEN: tuple = (None, None)     # ((path, mtime_ns, size), PlatformConfig)


def seen() -> PlatformConfig:
    """`load()`, re-read only when the file changed (its path, mtime and
    size): for a reader on a hot path — the worker's round reads `[modes]`
    (app/node_mode.py), as `config._file_models` reads `[models]`. A file that
    does not load gives the last one that did, the defaults where there was
    none: the System page says a broken file, a round does not stop on it."""
    global _SEEN
    path = platform_path()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return PlatformConfig()
    if _SEEN[0] != key:
        try:
            _SEEN = (key, _load_one(path))
        except Exception:   # noqa: BLE001 — said by the System page (its own load)
            _SEEN = (key, _SEEN[1] if _SEEN[1] is not None else PlatformConfig())
    return _SEEN[1]
