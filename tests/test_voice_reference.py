"""tools/voice_reference.py: a reference clip of about 10 s from a longer
recording, keeping the speaker's own pauses (operator, 2026-11-12)."""
from __future__ import annotations

import array
import io
import json
import math
import random
import urllib.request
import wave

import pytest

from tools import voice_reference as vr

RATE = 16000
LEAD = 0.40
# (seconds of speech, its words, seconds of pause after it)
LAYOUT = [
    (1.2, "Bonjour à tous,", 0.25),
    (1.5, "je suis Philippe.", 0.60),
    (0.9, "Aujourd'hui", 0.18),
    (1.6, "nous parlons de la loi,", 0.30),
    (1.4, "et de ses articles.", 0.70),
    (1.8, "Chaque mot compte vraiment.", 0.65),
    (1.1, "Merci,", 0.28),
    (1.3, "à bientôt.", 0.50),
]
TEXTS = [t for _, t, _ in LAYOUT]


def recording() -> tuple[vr.Audio, list[tuple[float, float]]]:
    """Room noise near -60 dBFS, 'speech' as a 180 Hz tone, one 60 ms dip
    inside part 3 (a consonant's closure, not a pause)."""
    rnd = random.Random(7)
    total = LEAD + sum(d + p for d, _, p in LAYOUT)
    n = round(total * RATE)
    s = [rnd.gauss(0.0, 30.0) for _ in range(n)]
    spans = []
    t = LEAD
    for k, (d, _, p) in enumerate(LAYOUT):
        a, b = round(t * RATE), round((t + d) * RATE)
        dip = (round((t + 0.7) * RATE), round((t + 0.76) * RATE)) if k == 3 else (0, 0)
        for i in range(a, b):
            if dip[0] <= i < dip[1]:
                continue
            env = min(1.0, (i - a) / 160, (b - i) / 160)
            s[i] += 8000.0 * env * math.sin(2 * math.pi * 180 * i / RATE)
        spans.append((t, t + d))
        t += d + p
    return vr.Audio(RATE, array.array("h", (max(-32768, min(32767, round(v))) for v in s))), spans


@pytest.fixture(scope="module")
def rec():
    return recording()


@pytest.fixture(scope="module")
def an(rec):
    audio, _ = rec
    return vr.analyse(audio, None, TEXTS, None)


def test_parts_are_found_at_their_pauses_and_a_short_dip_is_not_one(rec, an):
    _, spans = rec
    assert len(an.parts) == len(LAYOUT)
    for p, (a, b) in zip(an.parts, spans):
        assert abs(p.start / RATE - a) <= 0.02 and abs(p.end / RATE - b) <= 0.02
    for i, (_, _, pause) in enumerate(LAYOUT[:-1]):
        assert abs(an.pause(i) / RATE - pause) <= 0.03


def test_the_kind_of_each_boundary_comes_from_the_words(an):
    assert [an.kind(i) for i in range(len(LAYOUT))] == [
        "clause", "sentence", "word", "clause", "sentence", "sentence", "clause", "sentence"]
    assert [an.starts(i) for i in range(len(LAYOUT))] == [
        "sentence", "clause", "sentence", None, "clause", "sentence", "sentence", "clause"]
    st = an.style()
    assert set(st["pauses"]) == {"word", "clause", "sentence"} and 0.1 < st["pause_share"] < 0.4


def test_every_plan_is_whole_sentences_joined_across_the_same_kind_of_boundary(an):
    found = list(vr.plans(an, 6.0, 8.0, 3, ("sentence",)))
    assert found
    for p in found:
        assert 6.0 * RATE <= vr.duration(an, p) <= 8.0 * RATE
        assert an.kind(p.runs[-1][1]) == "sentence"
        for i, _ in p.runs:
            assert an.starts(i) is not None
        for (_, a), (b, _) in zip(p.runs, p.runs[1:]):
            assert b >= a + 2 and an.kind(a) == an.kind(b - 1) in ("sentence", "clause")


def test_the_clip_keeps_every_pause_at_its_own_length(rec, an):
    audio, _ = rec
    best, said = vr.choose(an, 7.0, 6.0, 8.0, 3, 3)
    assert best and said == ""
    for c in best:
        plan = c["plan"]
        clip = vr.render(audio, an, plan)
        assert len(clip) == vr.duration(an, plan)
        again = vr.find_parts(vr.Audio(RATE, clip), an.threshold_db)
        assert len(again.parts) == len(plan.parts())
        want = [p for p, _, _ in vr.pauses_of(an, plan)]
        got = [again.pause(i) for i in range(len(again.parts) - 1)]
        assert all(abs(g - w) <= 0.02 * RATE for g, w in zip(got, want))


def test_candidates_differ_and_rank_by_their_score(an):
    best, _ = vr.choose(an, 7.0, 6.0, 8.0, 3, 3)
    sets = [set(c["plan"].parts()) for c in best]
    for i in range(len(sets)):
        for j in range(i):
            assert len(sets[i] & sets[j]) / len(sets[i] | sets[j]) <= 2 / 3
    assert [c["score"] for c in best] == sorted((c["score"] for c in best), reverse=True)


def test_no_plan_fits_says_what_would(an):
    best, said = vr.choose(an, 30.0, 29.0, 31.0, 1, 3)
    assert best == [] and "--runs 2" in said


def test_words_are_placed_in_their_parts_despite_what_the_recogniser_missed():
    transcript = " ".join(TEXTS)
    heard = ["bonjour a tous", "je suis philippe", "", "nous parlons de la loi", "et de ces articles",
             "chaque mot compte", "merci", "a bientot"]
    texts, shares = vr.place(transcript, heard)
    assert texts == TEXTS
    assert shares[2] == 0.0 and shares[0] == 1.0 and shares[4] == 0.75


def test_an_unheard_word_between_two_heard_parts_goes_after_the_punctuation():
    texts, _ = vr.place("Merci, à bientôt.", ["merci", "bientot"])
    assert texts == ["Merci,", "à bientôt."]


def test_analyse_places_the_transcript_with_the_recogniser(rec):
    audio, _ = rec
    heard = iter(["bonjour à tous", "je suis", "", "nous parlons de la loi", "et de ses articles",
                  "chaque mot compte vraiment", "merci", "à bientôt"])
    an = vr.analyse(audio, " ".join(TEXTS), None, lambda s: next(heard))
    assert [p.text for p in an.parts] == TEXTS
    assert an.parts[1].matched == pytest.approx(2 / 3, abs=1e-3)
    assert any("parts [2]" in n for n in an.notes)


def test_without_words_the_kinds_are_guessed_and_said(rec):
    audio, _ = rec
    an = vr.analyse(audio, None, None, None)
    assert an.kinds_guessed and any("--stt or --texts" in n for n in an.notes)


def test_the_recogniser_is_asked_a_16k_wav_with_the_model(monkeypatch):
    seen = {}

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        seen["url"], seen["body"], seen["ctype"] = req.full_url, req.data, req.headers["Content-type"]
        return Answer(json.dumps({"text": " Bonjour "}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    s = array.array("h", [0] * 24000)
    assert vr.stt("http://m:8080/", "parakeet", s, 24000, "fr") == "Bonjour"
    assert seen["url"] == "http://m:8080/v1/audio/transcriptions"
    assert seen["ctype"].startswith("multipart/form-data; boundary=")
    body = seen["body"]
    assert b'name="model"\r\n\r\nparakeet' in body and b'name="language"\r\n\r\nfr' in body
    wav_part = body[body.index(b"RIFF"):]
    with wave.open(io.BytesIO(wav_part), "rb") as w:
        assert w.getframerate() == 16000 and w.getnframes() == 16000


def test_the_command_line_writes_the_clips_and_their_transcripts(rec, tmp_path):
    audio, _ = rec
    src = tmp_path / "orig.wav"
    src.write_bytes(vr.wav_bytes(audio.samples, RATE))
    (tmp_path / "texts.json").write_text(json.dumps(TEXTS), encoding="utf-8")
    out = tmp_path / "out"
    assert vr.main(["analyse", str(src), "--texts", str(tmp_path / "texts.json"), "--out", str(out)]) == 0
    assert vr.main(["assemble", str(src), str(out / "analysis.json"), "--target", "7", "--min", "6",
                    "--max", "8", "--out", str(out)]) == 0
    rep = json.loads((out / "candidates.json").read_text(encoding="utf-8"))
    assert rep["candidates"]
    for row in rep["candidates"]:
        n = row["candidate"]
        txt = (out / f"candidate-{n}.txt").read_text(encoding="utf-8").strip()
        assert txt == row["transcript"] == " ".join(TEXTS[k] for k in row["parts"])
        clip = vr.read_wav(out / f"candidate-{n}.wav")
        assert clip.rate == RATE and len(clip.samples) == row["samples"]
        assert 6.0 <= row["duration_s"] <= 8.0


def test_a_wav_it_cannot_read_says_how_to_convert_it(tmp_path):
    p = tmp_path / "stereo.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"\0\0" * 200)
    with pytest.raises(SystemExit, match="ffmpeg -i"):
        vr.read_wav(p)


# ---- longer sentence pauses (operator, 2026-11-12: Philippe's 1d, "add it")

def test_two_parts_meet_on_the_first_ones_own_pause(rec, an):
    """Corrected 2026-11-12: candidate 1 joined "vittime?" to the next
    sentence on the pause before that sentence (240 ms), not on the
    speaker's own 780 ms after it."""
    audio, _ = rec
    plan = vr.plan_of([0, 1, 5], an)
    assert plan.runs == [(0, 1), (5, 5)]
    assert [p for p, _, j in vr.pauses_of(an, plan) if j] == [an.pause(1)]
    again = vr.find_parts(vr.Audio(RATE, vr.render(audio, an, plan)), an.threshold_db)
    assert abs(again.pause(1) - an.pause(1)) <= 0.02 * RATE


def test_the_room_tone_is_the_quietest_stretch_of_a_pause_away_from_any_sound(rec, an):
    audio, _ = rec
    lo, hi = vr.room_tone(audio, an)
    assert hi - lo == round(vr.ROOM_TONE_S * RATE)
    gaps = [(0, an.parts[0].start)] + [(an.parts[i].end, an.parts[i + 1].start) for i in range(len(an.parts) - 1)]
    margin = vr.TONE_MARGIN_S * RATE
    assert any(a + margin <= lo and hi <= b - margin for a, b in gaps)


@pytest.mark.parametrize("target", [1.0, 1.5])
def test_a_sentence_pause_is_lengthened_keeping_the_speakers_own_pause_whole(rec, an, target):
    audio, _ = rec
    plan = vr.plan_of([0, 1, 5, 6, 7], an)
    tone = vr.room_tone(audio, an)
    clip = vr.render(audio, an, plan, target, tone)
    ps = vr.pauses_of(an, plan)
    extra = sum(max(0, round(target * RATE) - p) for p, k, _ in ps if k == "sentence")
    assert len(clip) == vr.duration(an, plan) + extra
    again = vr.find_parts(vr.Audio(RATE, clip), an.threshold_db)
    assert len(again.parts) == len(plan.parts()), "the room tone is not taken for speech"
    got = [again.pause(i) for i in range(len(again.parts) - 1)]
    for g, (p, k, _) in zip(got, ps):
        want = max(p, round(target * RATE)) if k == "sentence" else p
        assert abs(g - want) <= 0.02 * RATE, (k, p, g, want)
    # the first half of his own pause after "Philippe." is there sample for sample
    x = round(vr.XFADE_S * RATE)
    s = audio.samples
    at = vr.lead(an, 0) + (an.parts[1].end - an.parts[0].start)
    half = an.pause(1) // 2
    assert clip[at:at + half - x] == s[an.parts[1].end:an.parts[1].end + half - x]
    d = vr.describe(an, plan, target)
    assert abs(d["duration_s"] - len(clip) / RATE) < 0.002
    assert [q["own_ms"] for q in d["pauses_ms"]] == [round(1000 * p / RATE) for p, _, _ in ps]


def test_without_a_target_nothing_is_lengthened(rec, an):
    audio, _ = rec
    plan = vr.plan_of([0, 1, 5], an)
    assert vr.render(audio, an, plan) == vr.render(audio, an, plan, None, vr.room_tone(audio, an))


def test_parts_out_of_range_are_refused(an):
    with pytest.raises(SystemExit, match="--parts"):
        vr.plan_of([0, 99], an)


def test_the_command_line_rebuilds_a_chosen_clip_with_longer_pauses(rec, tmp_path):
    audio, _ = rec
    src = tmp_path / "orig.wav"
    src.write_bytes(vr.wav_bytes(audio.samples, RATE))
    (tmp_path / "texts.json").write_text(json.dumps(TEXTS), encoding="utf-8")
    out = tmp_path / "out"
    assert vr.main(["analyse", str(src), "--texts", str(tmp_path / "texts.json"), "--out", str(out)]) == 0
    assert vr.main(["assemble", str(src), str(out / "analysis.json"), "--parts", "0,1,5",
                    "--sentence-pause", "1.0", "--room-tone", "8.50-8.70", "--out", str(out / "1d")]) == 0
    rep = json.loads((out / "1d" / "candidates.json").read_text(encoding="utf-8"))
    (row,) = rep["candidates"]
    assert row["parts"] == [0, 1, 5] and rep["sentence_pause_s"] == 1.0 and rep["room_tone_s"] == [8.5, 8.7]
    assert [q["ms"] for q in row["pauses_ms"] if q["kind"] == "sentence"] == [1000]
    clip = vr.read_wav(out / "1d" / "candidate-1.wav")
    assert abs(len(clip.samples) / RATE - row["duration_s"]) < 0.002
    assert (out / "1d" / "candidate-1.txt").read_text(encoding="utf-8").strip() == " ".join(TEXTS[k] for k in (0, 1, 5))


def test_a_room_tone_given_inside_speech_is_refused(rec, tmp_path):
    audio, _ = rec
    src = tmp_path / "orig.wav"
    src.write_bytes(vr.wav_bytes(audio.samples, RATE))
    (tmp_path / "texts.json").write_text(json.dumps(TEXTS), encoding="utf-8")
    assert vr.main(["analyse", str(src), "--texts", str(tmp_path / "texts.json"), "--out", str(tmp_path)]) == 0
    with pytest.raises(SystemExit, match=r"overlaps part\(s\) \[1\]"):
        vr.main(["assemble", str(src), str(tmp_path / "analysis.json"), "--parts", "0,1,5",
                 "--sentence-pause", "1.0", "--room-tone", "2.95-3.15", "--out", str(tmp_path / "x")])
