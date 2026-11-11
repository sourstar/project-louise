"""A voice's reference clip, about 10 s, assembled from the parts of a longer
recording that best keep the speaker: the voice AND the way of pausing
(operator, 2026-11-12, of Philippe's 27.75 s original: "assemble the most
meaningful parts of the whole clip in a clip of about 10s. Beyond the pure
voice to be, the speaker has a distinct style way to make pauses between
words and sentences").

WHAT IT KEEPS
- Every pause in the result is one of the speaker's own, at its own length
  and with its own audio (room tone, breath). Nothing is trimmed, stretched
  or replaced by digital silence. Inside a part, the pauses are the
  original's. Where two parts meet, the pause is the one the speaker made
  after the first part, at its own length (corrected 2026-11-12: it was the
  pause before the second part, 240 ms instead of his 780 ms after
  "vittime?" in Philippe's candidate 1); its last HOLD_S are the second
  part's own lead-in. Two parts meet only where both sides are the same kind
  of boundary: a sentence end meets a sentence start, a clause end meets a
  clause start.
- Parts are whole sentences or clauses: each starts after a sentence or
  clause boundary, and the clip ends on a sentence end. The model hears
  the speaker's whole intonation contour, never a cut one.
- The transcript is the recording's own words: the known transcript, each
  word placed in its part by the node's speech-to-text. It matches the
  audio, and OmniVoice clones from the pair.

HOW CANDIDATES ARE RANKED (for listening: the ear decides, not the score)
  style     the pause share of the result against the original's, and how
            many of the original's kinds of pause (word, clause, sentence)
            it holds
  coverage  distinct letter pairs of its words against the original's, a
            proxy for the sounds it covers
  level     its parts within LEVEL_DB of the recording's median speech
            level (no trailing off)
  whole     every part starts a sentence (not only a clause)
  joins     fewer joins (each one a splice)
  length    nearer the target

THE STANDARDS USED
- Pauses: a level below a threshold for a minimum duration, the measure
  of ffmpeg's `silencedetect`, with the threshold set from this
  recording's own noise floor and speech level instead of a fixed dB. The
  node's silero VAD (app/transcribe.py) is not used: it finds speech for a
  recogniser and merges short pauses, and here the length of each pause
  between words is the thing kept. A forced aligner (Montreal Forced
  Aligner, WhisperX) would time every word. It is not on the node, and the
  parts need only their words, not each word's time: worth adopting if a
  reference must ever be cut inside a sentence.
- Word placement: difflib's SequenceMatcher (Ratcliff/Obershelp) between
  the known transcript and the speech-to-text of each part.
- Splices: a XFADE_S linear crossfade inside a pause; the pause keeps its
  exact length.

LONGER SENTENCE PAUSES (operator, 2026-11-12: Philippe's live clip "1d",
then "add it"): `assemble --sentence-pause 1.0` makes every sentence pause
of the clip at least 1.0 s. The speaker's own pause is kept whole, speech
tails included; the missing length is the recording's quietest room tone
(`--room-tone START-END`, else found: ROOM_TONE_S at TONE_MARGIN_S from any
sound), inserted at the pause's middle with the same crossfades, repeated
if one is not enough. `--parts 0,1,2,10,11` writes exactly those parts, to
rebuild a clip chosen before.

Stdlib only, so it runs on the host or in any container:

  python3 tools/voice_reference.py analyse ORIG.wav --transcript ORIG.txt \\
      --stt http://localai:8080 --stt-model <stt model> --out DIR
  python3 tools/voice_reference.py assemble ORIG.wav DIR/analysis.json --out DIR
  python3 tools/voice_reference.py check DIR --stt ... --stt-model ...
  python3 tools/voice_reference.py assemble ORIG.wav DIR/analysis.json --parts 0,1,2,10,11 \\
      --sentence-pause 1.0 --room-tone 3.90-4.10 --out DIR/1d

`analyse` writes DIR/analysis.json: the parts, their pauses and their words.
It is meant to be read and corrected by hand where a part's words are
flagged. `--texts FILE` (a JSON list, one text per part) replaces the
placement. `assemble` writes candidate-<n>.wav and .txt, plus
candidates.json. `check` transcribes each candidate and compares the result
with its .txt.
"""
from __future__ import annotations

import argparse
import array
import difflib
import io
import json
import math
import sys
import unicodedata
import urllib.request
import uuid
import wave
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

FRAME_S = 0.010        # one level per 10 ms
MIN_PAUSE_S = 0.12     # a quieter stretch this long is a pause, not a consonant's closure
MIN_SPEECH_S = 0.08    # a louder stretch shorter than this (a click) is not speech
XFADE_S = 0.010
HOLD_S = 0.05          # the lead-in a part keeps where it follows another part
LEAD_S = 0.20
TAIL_S = 0.30
LEVEL_DB = 6.0
ROOM_TONE_S = 0.20     # the room tone a lengthened pause is filled from
TONE_MARGIN_S = 0.05   # ... taken at least this far from any sound
STT_RATE = 16000
SENTENCE_END = ".!?…"
CLAUSE_END = ",;:—–"
WEIGHTS = {"style": 0.35, "coverage": 0.20, "level": 0.15, "whole": 0.10, "joins": 0.10, "length": 0.10}


# ---------------------------------------------------------------- audio

@dataclass
class Audio:
    rate: int
    samples: array.array   # int16, mono


def read_wav(path: str | Path) -> Audio:
    with wave.open(str(path), "rb") as w:
        ch, width, rate, comp = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getcomptype()
        if (ch, width, comp) != (1, 2, "NONE"):
            raise SystemExit(f"{path}: {ch} channel(s), {8 * width}-bit {comp}. This tool reads 16-bit PCM mono; "
                             f"convert it first: ffmpeg -i {path} -ac 1 -c:a pcm_s16le <out>.wav")
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
    if sys.byteorder == "big":
        a.byteswap()
    return Audio(rate, a)


def wav_bytes(samples: array.array, rate: int) -> bytes:
    s = array.array("h", samples)
    if sys.byteorder == "big":
        s.byteswap()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(s.tobytes())
    return buf.getvalue()


def resample(samples: array.array, rate: int, to: int) -> array.array:
    """Linear interpolation, enough for a recogniser to read; never used on
    what is written as a reference."""
    if rate == to or not samples:
        return array.array("h", samples)
    n = max(1, int(len(samples) * to / rate))
    last = len(samples) - 1
    out = array.array("h", bytes(2 * n))
    for k in range(n):
        pos = k * rate / to
        i = min(int(pos), last)
        f = pos - i
        j = min(i + 1, last)
        out[k] = round(samples[i] * (1.0 - f) + samples[j] * f)
    return out


def frame_db(a: Audio) -> list[float]:
    n = max(1, round(FRAME_S * a.rate))
    s = a.samples
    out = []
    for i in range(0, len(s), n):
        chunk = s[i:i + n]
        rms = math.sqrt(sum(v * v for v in chunk) / len(chunk)) / 32768.0
        out.append(20.0 * math.log10(rms) if rms > 1e-5 else -100.0)
    return out


def percentile(xs: list[float], q: float) -> float:
    ys = sorted(xs)
    if not ys:
        return 0.0
    k = (len(ys) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return ys[lo] + (ys[hi] - ys[lo]) * (k - lo)


def threshold(db: list[float]) -> tuple[float, float, float]:
    """(threshold, floor, speech): 30 % of the way from this recording's
    noise floor (10th percentile of its levels) to its speech (95th), at
    least 6 dB above the floor and 10 dB below the speech."""
    floor, loud = percentile(db, 0.10), percentile(db, 0.95)
    span = loud - floor
    if span < 16.0:
        return floor + span / 2.0, floor, loud
    return min(max(floor + 0.3 * span, floor + 6.0), loud - 10.0), floor, loud


# ---------------------------------------------------------------- analysis

@dataclass
class Part:
    start: int              # first sample of its first sound
    end: int                # one past its last sound
    level_db: float
    text: str = ""
    matched: float | None = None   # the share of its words the speech-to-text heard


@dataclass
class Analysis:
    rate: int
    samples: int
    threshold_db: float
    floor_db: float
    speech_db: float
    parts: list[Part]
    kinds_guessed: bool = False
    notes: list[str] = field(default_factory=list)

    def pause(self, i: int) -> int:
        """Samples of silence after part i (to the next part, or the end)."""
        nxt = self.parts[i + 1].start if i + 1 < len(self.parts) else self.samples
        return nxt - self.parts[i].end

    def before(self, i: int) -> int:
        return self.parts[i].start - (self.parts[i - 1].end if i else 0)

    def kind(self, i: int) -> str:
        """The kind of boundary after part i: sentence, clause or word."""
        if not self.kinds_guessed:
            t = self.parts[i].text.rstrip().rstrip("\"'»”)")
            if t and t[-1] in SENTENCE_END:
                return "sentence"
            if t and t[-1] in CLAUSE_END:
                return "clause"
            if i == len(self.parts) - 1 and not t:
                return "sentence"
            return "word"
        if i == len(self.parts) - 1:
            return "sentence"
        inner = [self.pause(k) for k in range(len(self.parts) - 1)]
        return "sentence" if self.pause(i) >= 2.0 * percentile(inner, 0.5) else "word"

    def starts(self, i: int) -> str | None:
        """What part i starts: a sentence, a clause, or None (mid-clause)."""
        if i == 0:
            return "sentence"
        k = self.kind(i - 1)
        return k if k in ("sentence", "clause") else None

    def style(self) -> dict[str, Any]:
        inner = list(range(len(self.parts) - 1))
        speech_span = self.parts[-1].end - self.parts[0].start if self.parts else 0
        by: dict[str, list[float]] = {}
        for i in inner:
            by.setdefault(self.kind(i), []).append(self.pause(i) / self.rate)
        return {"pause_share": round(sum(self.pause(i) for i in inner) / speech_span, 4) if speech_span else 0.0,
                "pauses": {k: {"count": len(v), "median_ms": round(1000 * percentile(v, 0.5)),
                               "min_ms": round(1000 * min(v)), "max_ms": round(1000 * max(v))}
                           for k, v in sorted(by.items())}}


def find_parts(a: Audio, threshold_db: float | None = None) -> Analysis:
    db = frame_db(a)
    thr, floor, loud = threshold(db)
    if threshold_db is not None:
        thr = threshold_db
    n = max(1, round(FRAME_S * a.rate))
    min_speech = max(1, round(MIN_SPEECH_S / FRAME_S))
    min_pause = max(1, round(MIN_PAUSE_S / FRAME_S))
    runs: list[list[int]] = []
    i = 0
    while i < len(db):
        if db[i] >= thr:
            j = i
            while j < len(db) and db[j] >= thr:
                j += 1
            if j - i >= min_speech:
                runs.append([i, j])
            i = j
        else:
            i += 1
    merged: list[list[int]] = []
    for r in runs:
        if merged and r[0] - merged[-1][1] < min_pause:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    parts = []
    for f0, f1 in merged:
        lv = [d for d in db[f0:f1]]
        parts.append(Part(start=f0 * n, end=min(f1 * n, len(a.samples)),
                          level_db=round(10 * math.log10(sum(10 ** (d / 10) for d in lv) / len(lv)), 2)))
    return Analysis(rate=a.rate, samples=len(a.samples), threshold_db=round(thr, 2), floor_db=round(floor, 2),
                    speech_db=round(loud, 2), parts=parts)


# ---------------------------------------------------------------- words

def norm(word: str) -> str:
    w = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in w if c.isalnum())


def ends(word: str) -> bool:
    """The word ends a sentence or a clause."""
    return word.rstrip("\"'»”)")[-1:] in SENTENCE_END + CLAUSE_END


def place(transcript: str, heard: list[str]) -> tuple[list[str], list[float | None]]:
    """The known transcript's words, part by part: each word goes to the
    part whose speech-to-text heard it (SequenceMatcher). A word nobody
    heard finishes the clause of the heard part before it, then goes to a
    part between its neighbours that nobody heard a word of; else with its
    neighbours, split after punctuation where the run falls between two
    parts. Returns (text per part, share of its words
    heard)."""
    ref = transcript.split()
    refn = [norm(w) for w in ref]
    hyp: list[str] = []
    owner: list[int] = []
    for u, t in enumerate(heard):
        for w in t.split():
            if norm(w):
                hyp.append(norm(w))
                owner.append(u)
    unit: list[int | None] = [None] * len(ref)
    sm = difflib.SequenceMatcher(None, refn, hyp, autojunk=False)
    for a0, b0, size in sm.get_matching_blocks():
        for k in range(size):
            if refn[a0 + k]:
                unit[a0 + k] = owner[b0 + k]
    heard_word = [u is not None for u in unit]
    i = 0
    while i < len(ref):
        if unit[i] is not None:
            i += 1
            continue
        j = i
        while j < len(ref) and unit[j] is None:
            j += 1
        u = unit[i - 1] if i else None
        v = unit[j] if j < len(ref) else None
        gap = j - i
        lo_p = 0 if u is None else u + 1
        hi_p = len(heard) - 1 if v is None else v - 1
        between = list(range(lo_p, hi_p + 1))
        if between:          # parts nobody heard a word of: the unheard words are most likely theirs,
            fill = []        # once the heard part before them has finished its clause
            if u is not None and not ends(ref[i - 1]):
                for k in range(i, j):
                    fill.append(u)
                    if ends(ref[k]):
                        break
                else:
                    fill = []
            rest = gap - len(fill)
            fill += [between[k * len(between) // rest] for k in range(rest)]
        elif u is None or v is None or u == v:
            fill = [v if u is None else u] * gap   # type: ignore[list-item]
        else:                # between two heard parts: cut after the last punctuation, else halve
            cut = None
            for k in range(i - 1, j):
                if ends(ref[k]):
                    cut = k + 1
            cut = i + gap // 2 if cut is None else cut
            fill = [u if i + k < cut else v for k in range(gap)]
        unit[i:j] = fill
        i = j
    texts = [" ".join(ref[k] for k in range(len(ref)) if unit[k] == p) for p in range(len(heard))]
    shares: list[float | None] = []
    for p in range(len(heard)):
        mine = [heard_word[k] for k in range(len(ref)) if unit[k] == p]
        shares.append(round(sum(mine) / len(mine), 3) if mine else None)
    return texts, shares


def multipart(fields: dict[str, str], wav: bytes) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    out.write(f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"part.wav\"\r\n"
              f"Content-Type: audio/wav\r\n\r\n".encode())
    out.write(wav)
    out.write(f"\r\n--{b}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={b}"


def stt(url: str, model: str, samples: array.array, rate: int, language: str | None = None,
        timeout: float = 180.0) -> str:
    """OpenAI's `POST /v1/audio/transcriptions` on the node's model server,
    a 16 kHz mono WAV."""
    fields = {"model": model, "response_format": "json"}
    if language:
        fields["language"] = language
    body, ctype = multipart(fields, wav_bytes(resample(samples, rate, STT_RATE), STT_RATE))
    req = urllib.request.Request(f"{url.rstrip('/')}/v1/audio/transcriptions", data=body,
                                 headers={"Content-Type": ctype}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return str((json.loads(r.read() or b"{}") or {}).get("text") or "").strip()


def part_audio(a: Audio, an: Analysis, i: int) -> array.array:
    """Part i with half of each pause around it, for the recogniser."""
    lo = an.parts[i].start - an.before(i) // 2
    hi = an.parts[i].end + an.pause(i) // 2
    return a.samples[max(0, lo):min(len(a.samples), hi)]


# ---------------------------------------------------------------- plans

@dataclass
class Plan:
    runs: list[tuple[int, int]]   # parts i..j, inclusive, in order

    def parts(self) -> list[int]:
        return [k for i, j in self.runs for k in range(i, j + 1)]


def lead(an: Analysis, i0: int) -> int:
    return min(round(LEAD_S * an.rate), an.before(i0))


def tail(an: Analysis, j: int) -> int:
    return min(round(TAIL_S * an.rate), an.pause(j))


def preroll(an: Analysis, i: int) -> int:
    """What part i keeps of its own lead-in where it follows another part:
    its first sound may be quieter than the threshold."""
    return min(round(HOLD_S * an.rate), an.before(i) // 2)


def pauses_of(an: Analysis, plan: Plan) -> list[tuple[int, str, bool]]:
    """(samples, kind, is a join) for every pause inside the result."""
    out = []
    for r, (i, j) in enumerate(plan.runs):
        if r:
            a = plan.runs[r - 1][1]
            out.append((an.pause(a), an.kind(a), True))
        out += [(an.pause(k), an.kind(k), False) for k in range(i, j)]
    return out


def duration(an: Analysis, plan: Plan) -> int:
    speech = sum(an.parts[j].end - an.parts[i].start for i, j in plan.runs)
    joins = sum(an.pause(j) for _, j in plan.runs[:-1])
    return lead(an, plan.runs[0][0]) + speech + joins + tail(an, plan.runs[-1][1])


def plans(an: Analysis, lo_s: float, hi_s: float, max_runs: int, ends: tuple[str, ...]) -> Iterator[Plan]:
    """Every plan of at most `max_runs` runs of whole parts, lasting
    lo_s..hi_s, that starts each run at a sentence or clause start, joins two
    runs only across the same kind of boundary, and ends on one of `ends`."""
    n = len(an.parts)
    lo, hi = round(lo_s * an.rate), round(hi_s * an.rate)
    starts = [i for i in range(n) if an.starts(i)]

    def grow(runs: list[tuple[int, int]]) -> Iterator[Plan]:
        last_j = runs[-1][1]
        d = duration(an, Plan(runs))
        if d > hi + round(TAIL_S * an.rate):
            return
        if lo <= d <= hi and an.kind(last_j) in ends:
            yield Plan(list(runs))
        if len(runs) >= max_runs or an.kind(last_j) not in ("sentence", "clause"):
            return
        for b in starts:
            if b < last_j + 2 or an.kind(b - 1) != an.kind(last_j):
                continue
            for j in range(b, n):
                runs.append((b, j))
                if duration(an, Plan(runs)) > hi + round(TAIL_S * an.rate):
                    runs.pop()
                    break
                yield from grow(runs)
                runs.pop()

    for i in starts:
        for j in range(i, n):
            if an.parts[j].end - an.parts[i].start > hi:
                break
            yield from grow([(i, j)])


def bigrams(text: str) -> set[str]:
    words = [norm(w) for w in text.split()]
    s = " " + " ".join(w for w in words if w) + " "
    return {s[k:k + 2] for k in range(len(s) - 1)}


def score(an: Analysis, plan: Plan, target_s: float, lo_s: float, hi_s: float, max_runs: int) -> dict[str, float]:
    st = an.style()
    po = st["pause_share"]
    ps = pauses_of(an, plan)
    speech = sum(an.parts[j].end - an.parts[i].start for i, j in plan.runs) + sum(p for p, _, j in ps if j)
    pc = sum(p for p, _, _ in ps) / speech if speech else 0.0
    kinds_o = set(st["pauses"])
    kinds_c = {k for _, k, _ in ps}
    share = 1.0 - min(1.0, abs(pc - po) / po) if po else 1.0
    style = 0.5 * share + 0.5 * (len(kinds_c & kinds_o) / len(kinds_o) if kinds_o else 1.0)
    full = " ".join(p.text for p in an.parts)
    cover = len(bigrams(" ".join(an.parts[k].text for k in plan.parts())) & bigrams(full)) / max(1, len(bigrams(full)))
    med = percentile([p.level_db for p in an.parts], 0.5)
    ks = plan.parts()
    level = sum(abs(an.parts[k].level_db - med) <= LEVEL_DB for k in ks) / len(ks)
    whole = sum(an.starts(i) == "sentence" for i, _ in plan.runs) / len(plan.runs)
    joins = 1.0 - (len(plan.runs) - 1) / max(1, max_runs - 1)
    d = duration(an, plan) / an.rate
    length = 1.0 - min(1.0, abs(d - target_s) / max(target_s - lo_s, hi_s - target_s, 1e-6))
    return {"style": round(style, 4), "coverage": round(cover, 4), "level": round(level, 4),
            "whole": round(whole, 4), "joins": round(joins, 4), "length": round(length, 4),
            "pause_share": round(pc, 4)}


def total(parts: dict[str, float], best_cover: float) -> float:
    p = dict(parts)
    p["coverage"] = p["coverage"] / best_cover if best_cover else 0.0
    return round(sum(WEIGHTS[k] * p[k] for k in WEIGHTS), 4)


def choose(an: Analysis, target_s: float, lo_s: float, hi_s: float, max_runs: int, top: int) -> tuple[list[dict], str]:
    """The `top` best plans, each differing from those before it in at least
    a third of its parts. The clip ends on a sentence; on a clause only
    where no plan ends on a sentence (said in the second value)."""
    for end_kinds, said in ((("sentence",), ""),
                       (("sentence", "clause"), "no plan ends on a sentence end: plans may end on a clause")):
        found = [(p, score(an, p, target_s, lo_s, hi_s, max_runs)) for p in plans(an, lo_s, hi_s, max_runs, end_kinds)]
        if found:
            break
    else:
        return [], (f"no plan of whole sentences or clauses lasts {lo_s}–{hi_s} s in at most {max_runs} parts: "
                    f"try --runs {max_runs + 1}, or a wider --min/--max")
    best_cover = max(s["coverage"] for _, s in found)
    ranked = sorted(found, key=lambda ps: (-total(ps[1], best_cover), ps[0].runs))
    out: list[dict] = []
    kept: list[set[int]] = []
    for p, s in ranked:
        ks = set(p.parts())
        if any(len(ks & k) / len(ks | k) > 2 / 3 for k in kept):
            continue
        kept.append(ks)
        out.append({"plan": p, "score": total(s, best_cover), "components": s})
        if len(out) == top:
            break
    return out, said


# ---------------------------------------------------------------- the clip

def plan_of(parts: list[int], an: Analysis) -> Plan:
    """The plan of exactly these parts (`assemble --parts`), in order, each
    run of consecutive parts one run."""
    ks = sorted(set(parts))
    if not ks or ks[0] < 0 or ks[-1] >= len(an.parts):
        raise SystemExit(f"--parts must name parts 0..{len(an.parts) - 1}")
    runs: list[tuple[int, int]] = []
    for k in ks:
        if runs and k == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], k)
        else:
            runs.append((k, k))
    return Plan(runs)


def room_tone(a: Audio, an: Analysis, seconds: float = ROOM_TONE_S) -> tuple[int, int]:
    """The recording's quietest `seconds` inside a pause, at least
    TONE_MARGIN_S from any sound: the room the speaker was in, with nothing
    said. Sample range [lo, hi)."""
    n = max(1, round(FRAME_S * a.rate))
    db = frame_db(a)
    w = max(1, round(seconds / FRAME_S))
    m = round(TONE_MARGIN_S / FRAME_S)
    gaps = [(0, an.parts[0].start)] if an.parts else [(0, len(a.samples))]
    gaps += [(an.parts[i].end, an.parts[i + 1].start) for i in range(len(an.parts) - 1)]
    gaps += [(an.parts[-1].end, len(a.samples))] if an.parts else []
    best: tuple[float, int] | None = None
    for lo, hi in gaps:
        f0, f1 = -(-lo // n) + m, hi // n - m
        for f in range(f0, f1 - w + 1):
            e = sum(10 ** (d / 10) for d in db[f:f + w])
            if best is None or e < best[0]:
                best = (e, f)
    if best is None:
        raise SystemExit(f"no pause holds {seconds} s of room tone at {TONE_MARGIN_S} s from any sound: "
                         f"give --room-tone START-END")
    return best[1] * n, best[1] * n + w * n


def pieces(an: Analysis, plan: Plan, sentence_pause_s: float | None = None,
           tone: tuple[int, int] | None = None) -> list[tuple[int, int]]:
    """The plan as ranges of the recording, in order: lead-in, runs, and
    between two runs the pause the speaker made after the first, at its own
    length: its audio but the last `preroll`, which is the second part's own
    lead-in. With
    `sentence_pause_s` (operator, 2026-11-12, Philippe's 1d), a sentence pause
    shorter than that is made that long: the speaker's own pause is kept
    whole, speech tails included, and the room tone `tone` is inserted at
    its middle, repeated if it must."""
    want = round(sentence_pause_s * an.rate) if sentence_pause_s else 0
    cuts: dict[int, list[tuple[int, int]]] = {r: [] for r in range(len(plan.runs))}
    if want:
        if tone is None:
            raise ValueError("lengthening a pause needs the room tone")
        for r, (i, j) in enumerate(plan.runs):
            for k in range(i, j):                      # a pause inside the run
                p = an.pause(k)
                if an.kind(k) == "sentence" and p < want:
                    cuts[r].append((an.parts[k].end + p // 2, want - p))
            if r:                                      # the pause where two runs meet: the first's own
                a = plan.runs[r - 1][1]
                p = an.pause(a)
                if an.kind(a) == "sentence" and p < want:  # its middle lies before the preroll
                    cuts[r - 1].append((an.parts[a].end + p // 2, want - p))
    out: list[tuple[int, int]] = []
    for r, (i, j) in enumerate(plan.runs):
        last = r == len(plan.runs) - 1
        lo = an.parts[i].start - (lead(an, i) if r == 0 else preroll(an, i))
        hi = an.parts[j].end + (tail(an, j) if last else an.pause(j) - preroll(an, plan.runs[r + 1][0]))
        for c, extra in sorted(cuts[r]):
            out.append((lo, c))
            ta, tb = tone or (0, 0)
            while extra > 0:
                take = min(extra, tb - ta)
                out.append((ta, ta + take))
                extra -= take
            lo = c
        out.append((lo, hi))
    return out


def render(a: Audio, an: Analysis, plan: Plan, sentence_pause_s: float | None = None,
           tone: tuple[int, int] | None = None) -> array.array:
    """The plan as audio (`pieces`), each splice a XFADE_S crossfade inside a
    pause that keeps the pause's length exact."""
    s = a.samples
    x = max(1, round(XFADE_S * a.rate))
    out = array.array("h")
    prev = None
    for lo, hi in pieces(an, plan, sentence_pause_s, tone):
        if prev is None or lo == prev:
            out.extend(s[lo:hi])
        else:
            seg = s[lo - x:hi]
            for k in range(x):
                t = (k + 0.5) / x
                out[len(out) - x + k] = round(out[len(out) - x + k] * (1.0 - t) + seg[k] * t)
            out.extend(seg[x:])
        prev = hi
    for k in range(min(x, len(out))):     # no click at either end
        t = (k + 0.5) / x
        out[k] = round(out[k] * t)
        out[len(out) - 1 - k] = round(out[len(out) - 1 - k] * t)
    return out


def describe(an: Analysis, plan: Plan, sentence_pause_s: float | None = None) -> dict[str, Any]:
    flagged = [k for k in plan.parts() if an.parts[k].matched is not None and an.parts[k].matched < 0.5]
    want = round(sentence_pause_s * an.rate) if sentence_pause_s else 0

    def made(p: int, k: str) -> int:
        return max(p, want) if k == "sentence" else p
    ps = pauses_of(an, plan)
    return {
        "runs_s": [[round((an.parts[i].start) / an.rate, 3), round(an.parts[j].end / an.rate, 3)] for i, j in plan.runs],
        "parts": plan.parts(),
        "duration_s": round((duration(an, plan) + sum(made(p, k) - p for p, k, _ in ps)) / an.rate, 3),
        "pauses_ms": [{"ms": round(1000 * made(p, k) / an.rate), "own_ms": round(1000 * p / an.rate), "kind": k,
                       "join": jn} for p, k, jn in ps],
        "transcript": " ".join(an.parts[k].text for k in plan.parts()).strip(),
        "check_words_of_parts": flagged,
    }


# ---------------------------------------------------------------- files

def save_analysis(an: Analysis, path: Path) -> None:
    d = asdict(an)
    d["style"] = an.style()
    d["parts"] = [dict(asdict(p), start_s=round(p.start / an.rate, 3), end_s=round(p.end / an.rate, 3),
                       pause_after_ms=round(1000 * an.pause(i) / an.rate), kind_after=an.kind(i))
                  for i, p in enumerate(an.parts)]
    path.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load_analysis(path: Path) -> Analysis:
    d = json.loads(path.read_text(encoding="utf-8"))
    parts = [Part(start=p["start"], end=p["end"], level_db=p["level_db"], text=p.get("text", ""),
                  matched=p.get("matched")) for p in d["parts"]]
    return Analysis(rate=d["rate"], samples=d["samples"], threshold_db=d["threshold_db"], floor_db=d["floor_db"],
                    speech_db=d["speech_db"], parts=parts, kinds_guessed=d.get("kinds_guessed", False),
                    notes=d.get("notes", []))


def analyse(a: Audio, transcript: str | None, texts: list[str] | None,
            hear: Callable[[array.array], str] | None, threshold_db: float | None = None) -> Analysis:
    an = find_parts(a, threshold_db)
    if texts is not None:
        if len(texts) != len(an.parts):
            raise SystemExit(f"--texts has {len(texts)} texts for {len(an.parts)} parts")
        for p, t in zip(an.parts, texts):
            p.text = t.strip()
    elif transcript and hear:
        heard = [hear(part_audio(a, an, i)) for i in range(len(an.parts))]
        placed, shares = place(transcript, heard)
        for p, t, m in zip(an.parts, placed, shares):
            p.text, p.matched = t, m
        weak = [i for i, p in enumerate(an.parts) if p.matched is not None and p.matched < 0.5]
        if weak:
            an.notes.append(f"parts {weak}: under half their words were heard; read their text against the audio")
    elif transcript:
        an.notes.append("a transcript without --stt cannot be placed part by part: kinds guessed from pause length")
    if not any(p.text for p in an.parts):
        an.kinds_guessed = True
        an.notes.append("no words: sentence ends guessed from pause length (twice the median); "
                        "a candidate needs its transcript for cloning: give --stt or --texts")
    return an


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("analyse", help="find the parts, their pauses and their words")
    a1.add_argument("wav")
    a1.add_argument("--transcript", help="the recording's known transcript (a text file)")
    a1.add_argument("--texts", help="a JSON list: one text per part, replacing the placement")
    a1.add_argument("--stt", help="the node's model server (LocalAI) URL")
    a1.add_argument("--stt-model")
    a1.add_argument("--language")
    a1.add_argument("--threshold-db", type=float, help="the pause level in dBFS, instead of the recording's own")
    a1.add_argument("--out", required=True)
    a2 = sub.add_parser("assemble", help="rank the plans and write the best as clips")
    a2.add_argument("wav")
    a2.add_argument("analysis")
    a2.add_argument("--target", type=float, default=10.0)
    a2.add_argument("--min", type=float, default=9.0)
    a2.add_argument("--max", type=float, default=11.0)
    a2.add_argument("--runs", type=int, default=3, help="at most this many parts joined (default 3)")
    a2.add_argument("--top", type=int, default=3)
    a2.add_argument("--parts", help="write exactly these parts (e.g. 0,1,2,10,11) instead of ranking plans")
    a2.add_argument("--sentence-pause", type=float, metavar="S",
                    help="make every sentence pause at least S seconds, filled with the room tone")
    a2.add_argument("--room-tone", metavar="START-END",
                    help="the room tone, in seconds of the recording (default: its quietest 0.2 s in a pause)")
    a2.add_argument("--out", required=True)
    a3 = sub.add_parser("check", help="transcribe each candidate and compare it with its transcript")
    a3.add_argument("dir")
    a3.add_argument("--stt", required=True)
    a3.add_argument("--stt-model", required=True)
    a3.add_argument("--language")
    args = ap.parse_args(argv)

    if args.cmd == "analyse":
        audio = read_wav(args.wav)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        transcript = Path(args.transcript).read_text(encoding="utf-8") if args.transcript else None
        texts = json.loads(Path(args.texts).read_text(encoding="utf-8")) if args.texts else None
        hear = None
        if args.stt:
            if not args.stt_model:
                raise SystemExit("--stt needs --stt-model")
            hear = lambda s: stt(args.stt, args.stt_model, s, audio.rate, args.language)
        an = analyse(audio, transcript, texts, hear, args.threshold_db)
        save_analysis(an, out / "analysis.json")
        print(json.dumps({"parts": len(an.parts), "threshold_db": an.threshold_db, "style": an.style(),
                          "notes": an.notes}, ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "assemble":
        audio = read_wav(args.wav)
        an = load_analysis(Path(args.analysis))
        if (an.rate, an.samples) != (audio.rate, len(audio.samples)):
            raise SystemExit("the analysis is of another recording (rate or length differs)")
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        if args.parts:
            plan = plan_of([int(k) for k in args.parts.split(",")], an)
            best, said = [{"plan": plan, "score": None, "components": None}], "the parts given (--parts)"
        else:
            best, said = choose(an, args.target, args.min, args.max, args.runs, args.top)
        tone = None
        if args.sentence_pause:
            if args.room_tone:
                lo_s, hi_s = (float(v) for v in args.room_tone.split("-"))
                tone = (round(lo_s * audio.rate), round(hi_s * audio.rate))
                over = [k for k, q in enumerate(an.parts) if q.start < tone[1] and tone[0] < q.end]
                if tone[1] - tone[0] < round(XFADE_S * audio.rate) or over:
                    raise SystemExit(f"--room-tone {args.room_tone}: " + (
                        f"it overlaps part(s) {over}, which are speech" if over else "too short"))
            else:
                tone = room_tone(audio, an)
        rows = []
        for n, c in enumerate(best, 1):
            clip = render(audio, an, c["plan"], args.sentence_pause, tone)
            (out / f"candidate-{n}.wav").write_bytes(wav_bytes(clip, audio.rate))
            d = describe(an, c["plan"], args.sentence_pause)
            (out / f"candidate-{n}.txt").write_text(d["transcript"] + "\n", encoding="utf-8")
            rows.append({"candidate": n, "score": c["score"], "components": c["components"], **d,
                         "samples": len(clip)})
        report = {"original": an.style(), "weights": WEIGHTS, "note": said, "candidates": rows,
                  "sentence_pause_s": args.sentence_pause,
                  "room_tone_s": [round(v / audio.rate, 3) for v in tone] if tone else None}
        (out / "candidates.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                             encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if rows else 1

    d = Path(args.dir)
    rep_path = d / "candidates.json"
    report = json.loads(rep_path.read_text(encoding="utf-8"))
    for row in report["candidates"]:
        clip = read_wav(d / f"candidate-{row['candidate']}.wav")
        heard = stt(args.stt, args.stt_model, clip.samples, clip.rate, args.language)
        want = [norm(w) for w in row["transcript"].split() if norm(w)]
        got = [norm(w) for w in heard.split() if norm(w)]
        row["heard"] = heard
        row["words_match"] = round(difflib.SequenceMatcher(None, want, got, autojunk=False).ratio(), 3)
        print(f"candidate {row['candidate']}: words match {row['words_match']}")
    rep_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
