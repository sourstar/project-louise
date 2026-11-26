"""tools/measure/park_behind_vega.py — parking behind the VeGa wrapper, the three
parts together (docs/notes/parking-step-2.md §11).

The run itself needs LocalAI's server and a llama.cpp backend built with the
CPU stand-in, which no test image holds. What is held here is what would make
it pass for the wrong reason: the words it counts in the wrapper's and the
engine's logs are the words they write, the model file it loads is one the
wrapper states a run for, and the note says what was seen and what was not."""
import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "measure" / "park_behind_vega.py"
SOURCE = TOOL.read_text(encoding="utf-8")
WRAPPER = (ROOT / "deploy" / "vega" / "vega_wrapper.py").read_text(encoding="utf-8")
ENGINE = (ROOT / "deploy" / "localai" / "patches" / "staged" / "0023-llama-cpp-park-when-told.patch").read_text(encoding="utf-8")
NOTE = (ROOT / "docs" / "notes" / "parking-step-2.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("park_behind_vega", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)       # imports only: main() is not run
    return module


def test_the_words_it_counts_are_the_wrapper_s_and_the_engine_s(tool):
    # the wrapper: a repeated load let through as the load in force, with the backend's answer; a load stated;
    # a wrapper process started
    assert tool.STANDS == "the load in force asked again" and tool.STANDS in WRAPPER
    assert "the backend: {said.message!r}" in WRAPPER and 'ALREADY_LOADED = "Model already loaded"' in WRAPPER
    assert "the backend: 'Model already loaded'" in SOURCE
    assert 'loaded on {device}, "' in WRAPPER and 'said("loaded on ")' in SOURCE
    assert "behind VeGa: listening on port {port}" in WRAPPER and 'said("behind VeGa: listening on port")' in SOURCE
    # the engine: weights that left and came back, of weights and never of attempts
    assert 'SRV_INF("weights parked in %.2f ms' in ENGINE and 'SRV_INF("weights unparked in %.2f ms' in ENGINE
    assert 'said("weights parked in")' in SOURCE and 'said("weights unparked in")' in SOURCE
    # the wrapper's lines go to stderr, which the package's run.sh of the harness keeps in a file per instance
    assert 'print(f"[vega] {message}", file=sys.stderr, flush=True)' in WRAPPER
    assert '2>>"$PBV_WRAPPER_LOG"' in SOURCE


def test_the_model_file_is_one_the_wrapper_states_a_run_for_and_the_same_run_at_every_load():
    """Each line was learnt from a refusal of the wrapper, and the seed from six loads with six run ids."""
    for line in ("gpu_layers: 0", "seed: 0", "  - parallel:1", "  - context_on_demand:true", "  - context_canary:true",
                 "park: true"):
        assert line in SOURCE, line
    assert "keep_context_ms:0" not in SOURCE.split("def lay_out")[1].split("def free_port")[0], \
        "the model keeps its context: `created` then counts the returns, one each"
    assert "shutil.copy2(weights" in SOURCE and "os.symlink" not in SOURCE, "the wrapper states no run for weights behind a link"
    # every vega model file staged for the node pins its seed, which is why the node is not in the case found here
    files = sorted((ROOT / "deploy" / "vega" / "stage" / "models").glob("vega-*.yaml"))
    assert len(files) >= 15 and all('"seed": 0' in f.read_text(encoding="utf-8") for f in files)


def test_it_sends_tagged_inputs_and_counts_cycles_by_the_engine(tool):
    assert issubclass(tool.Tagged, tool.park.Server)
    assert "client.tag_batch(list(texts or park.TEXTS))" in SOURCE
    assert "an input without a VeGa tag" in (ROOT / "app" / "vega" / "tag.py").read_text(encoding="utf-8")
    assert tool.park.COUNT_KEYS == ("parks", "unparks", "no_room", "parked")
    # the row for a failed return passes only when `cycles` FAILS for the right reason
    assert 'not passed and "the engine\'s counts started again" in lines[-1]' in SOURCE
    tree = ast.parse(SOURCE)
    assert "main" in {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}


def test_the_note_says_what_was_seen_and_what_was_not():
    section = NOTE[NOTE.index("## 11. Behind the VeGa wrapper: the three parts together (2026-11-26)"):]
    for said in ("**25 of 25.**", "with the wrapper AS A BACKEND IT STARTS (`run.sh --addr`, as on\nthe node)",
                 "| 1 | a park passes the wrapper | 200; the ENGINE counts `parks` 1, `parked` 1",
                 "| 2 | the return's `LoadModel` is the load in force |", "ONE load stated;",
                 "| 3 | that Embedding has its receipt, and one context was created | a receipt for it; the wrapper's `created` 1 → 2 |",
                 "| 4 | a return that FAILS |", "THE RUN ID IS THE SAME",
                 "| 5 | a park inside the idle second | 409 `model_busy`",
                 "41 repeated loads let through, ONE load stated; created 1 -> 42, canary_failed 0",
                 "nothing in a receipt tells it from a\ncycle", "six loads,\nsix run ids, differing in `Seed` alone",
                 "**NOT shown:** a card."):
        assert said in section, said
    assert 1 + 20 + 20 == 41
