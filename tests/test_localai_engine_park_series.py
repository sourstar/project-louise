"""The engine a ROLLBACK puts back since 2026-11-26 23:32Z, ed93e3c1b2e2 (live from
2026-11-25 23:45:20Z; the operator: "Do parking."): 792f050e318e's series, then parking when told (0023)
(deploy/localai/patches/staged/engine-park.series), on LocalAI's server
v5.0.1-6-gffb10fdf3 (the node's tree + staged 0024, 0027, 0029).

An engine's name is the digest of its series file and of every patch in it,
comments included. Held here: that the file and its patches still name the
engine that is running, what it carries over the engine a rollback puts back,
that the notes say which engine and which server are live - and that parking
itself is OFF: being able to park is not parking. What 0023 is and how it was
measured is tests/test_localai_staged_park_when_told.py's."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
SERIES = PATCHES / "staged" / "engine-park.series"
STREAM = PATCHES / "staged" / "engine-stream.series"
ROLLBACK = PATCHES / "staged" / "engine-canary.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
LIVE = "ed93e3c1b2e2"   # live until 2026-11-26 23:32Z; the rollback engine since (tests/test_localai_engine_fade_series.py)
PARK = "staged/0023-llama-cpp-park-when-told.patch"
CANARY = "staged/0028-llama-cpp-context-canary-refused-for-memory.patch"


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def test_the_file_and_its_patches_still_name_the_engine_that_runs():
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(SERIES), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == LIVE, (
        "engine-park.series or one of its patches changed: that is ANOTHER engine than the one a rollback puts "
        "back, and the live engine's series starts with its lines")


def test_it_is_the_stream_series_and_parking_which_stands_in_for_the_canary_patch():
    assert _lines(SERIES) == _lines(STREAM) + [PARK]
    # against the engine a rollback puts back: the same lines but the last - 0023 where it has 0028, whose fix
    # 0023 carries in its own form on the same lines (tests/test_localai_staged_canary_refused.py holds the two)
    assert _lines(ROLLBACK) == _lines(STREAM) + [CANARY]
    assert CANARY not in _lines(SERIES)
    # nothing of LocalAI's server is in an engine
    for absent in ("0024-model-readiness-poll", "0027-model-park", "0029-watchdog-settings-update-hang"):
        assert not any(absent in line for line in _lines(SERIES)), absent


def test_the_readme_and_the_status_say_what_is_live_and_what_a_rollback_puts_back():
    readme = (PATCHES / "staged" / "README.md").read_text(encoding="utf-8")
    assert f"Live 2026-11-25 23:45:20Z to 2026-11-26 23:32Z, engine `{LIVE}`, and the engine a rollback puts back: `engine-park.series`" in readme
    assert "`v5.0.1-6-gffb10fdf3` = the node's tree + staged 0024, 0027, 0029" in readme
    assert "engine `619655e5833b`: `engine-canary.series`** (the rollback engine until 2026-11-26 23:32Z)" in readme
    assert readme.count("LIVE since ") == 1, "one engine is live"
    status = (ROOT / "docs" / "notes" / "STATUS.md").read_text(encoding="utf-8")
    assert f"Live 2026-11-25 23:45:20Z to 2026-11-26 23:32Z, and the engine a rollback puts back: engine `{LIVE}`" in status
    assert status.count("and the engine a rollback puts back") == 1
    assert "every fingerprint identical to before" in status and "20 × exit 0" in status
    # what the swap did NOT do
    assert "PARKING IS OFF: no model's file says `park: true`, the\n  floor is 0" in status
    # the pass's end, and parking's rows on copies (docs/notes/parking-step-2.md §9)
    assert "THE PASS ENDED 2026-11-26 00:19:39Z, the node paused 5,620 s" in status
    assert "NeuRIS (nemotron) 509 s, wikidata (nemotron) 508 s, worst 1.000000 each" in status
    card = status[status.index("- **PARKING ON THE CARD, for the first time through the server"):]
    card = card[:card.index("\n- **", 5)]
    for said in ("A PARK GIVES THE CARD BACK 996 OF 1,315 MiB for 577 MiB of host memory", "1,084 × 409 `model_busy`",
                 "NOT MEASURED: the card's counter while parked", "the\n  gate's clause with the fingerprint texts",
                 "NO REAL MODEL PARKS: both copies are removed"):
        assert said in card, said
    assert "being taken" not in card and "not claimed here" not in status[status.index(f"engine `{LIVE}`"):][:3000]
    assert "ON THE CARD, ON COPIES (München, the same pass, ended 2026-11-26\n00:19:39Z)" in readme
    assert "docs/notes/parking-step-2.md §6, §8, §9." in readme


def test_a_staged_model_that_parks_is_one_the_engine_can_park():
    """Which staged models park, and that the three lines go together, is held by tests/test_staged_parking.py
    (main, with the first of them: vega-bge-m3, 2026-11-26). What is held HERE is the engine's side of such a
    file: the park engine parks only a model that takes its context on demand (staged 0023: "this model cannot
    park: it takes context on demand and weights that can leave the device"), and the switch is read by the
    live engine's series and no other."""
    files = [f for d in sorted((ROOT / "deploy").glob("*/models")) + [ROOT / "deploy" / "vega" / "stage" / "models"]
             for f in sorted(d.glob("*.yaml"))]
    assert len(files) > 40
    parking = []
    for f in files:
        body = "\n".join(line for line in f.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))
        if "GGML_SYCL_PARKABLE" not in body and '"park": true' not in body and "\npark: true" not in body:
            continue
        parking.append(f.name)
        assert '"GGML_SYCL_PARKABLE": "1"' in body and '"park": true' in body, f.name
        assert '"context_on_demand"' in body or '"context_on_demand:true"' in body, \
            f"{f.name}: the engine parks only a model on context on demand"
        assert '"context_canary:true"' in body, f"{f.name}: a return's context is held to the canary"
        assert "GGML_SYCL_PARK_REFUSE" not in body, f"{f.name}: the refusal switch is for copies, never a staged model"
    assert parking == ["vega-bge-m3.yaml"], parking
    patch = (PATCHES / "staged" / "0023-llama-cpp-park-when-told.patch").read_text(encoding="utf-8")
    assert "GGML_SYCL_PARKABLE" in patch
    assert "this model cannot park: it takes context on demand and weights that can leave the device" in patch
    assert PARK in _lines(SERIES) and PARK not in _lines(ROLLBACK), "a rollback puts back an engine that does not park"
    # ... which is why a rollback has a step more since a real model parks: said where a rollback is read
    readme = (PATCHES / "staged" / "README.md").read_text(encoding="utf-8")
    assert "A ROLLBACK to THIS engine keeps parking (it is\nthe one that parks); one further back, to `619655e5833b`, which does not park, takes the three lines out of every parking model's file\nfirst" in readme
    note = (ROOT / "docs" / "notes" / "parking-step-2.md").read_text(encoding="utf-8")
    section = note[note.index("## 13. The first real model parks: vega-bge-m3 (2026-11-26 08:18Z)"):]
    for said in ("THE SAME RUN ID AS THE COPIES", "parking 64 park cycles, each counted by the engine; closest 1.0000000",
                 "**A ROLLBACK NOW HAS ONE STEP MORE.**", "REFUSES the run", "**NOT shown, still:** the keeper's first park of a real model"):
        assert said in section, said
    # the gate's own words for that refusal, on main
    gate = (ROOT / "app" / "hold" / "embedder.py").read_text(encoding="utf-8")
    assert "the model's file says `park: true` and the runner will not park it" in gate
