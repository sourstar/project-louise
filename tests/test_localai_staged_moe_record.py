"""The staged engine patch `0017-llama-cpp-moe-record.patch`: the MoE cache is
filled, when it is created, from a record of the experts used
(docs/notes/moe-expert-record.md; operator, 2026-11-24).

The fill cannot run where it is written: the cache needs a card beside the
host. Held here: that nothing happens unless a model's env names the
record's directory, that the fill is in the cache's creation and the file's
write in its destruction — never on the token path — and the record's own
test, taken from the patch, built and run."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
STAGED = PATCHES / "staged"
PATCH = STAGED / "0017-llama-cpp-moe-record.patch"
NOTE = (ROOT / "docs" / "notes" / "moe-expert-record.md").read_text(encoding="utf-8")
INNER = "backend/cpp/llama-cpp/patches/0017-moe-record.patch"


def _added(patch: str) -> list[str]:
    return [line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]


def _inner() -> str:
    outer = PATCH.read_text(encoding="utf-8")
    return "\n".join(_added(outer[outer.index("\n---\n"):]))


def _files() -> dict[str, str]:
    """{path: the llama.cpp patch's diff for it}."""
    inner = _inner()
    parts = re.split(r"^diff --git a/(\S+) b/\S+\n", inner[inner.index("\n---\n"):], flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def _new_file(diff: str) -> str:
    assert "new file mode" in diff
    body = diff[diff.index("\n@@ "):].split("\n", 2)[2]
    return "\n".join(line[1:] for line in body.splitlines() if line.startswith("+")) + "\n"


def test_it_is_a_mail_under_localai_s_policy_and_adds_one_engine_patch():
    text = PATCH.read_text(encoding="utf-8")
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert re.findall(r"^diff --git a/(\S+)", text, re.M) == [INNER]
    assert "has not run where" in message, "the fill needs a GPU beside the host: said, not hidden"
    assert set(_files()) == {"src/llama-model.cpp", "src/llama-model.h", "src/llama-moe-cache.cpp",
                             "src/llama-moe-record.h", "tests/CMakeLists.txt", "tests/test-moe-record.cpp"}


def test_nothing_is_read_kept_or_filled_without_the_model_s_env():
    cache = "\n".join(_added(_files()["src/llama-moe-cache.cpp"]))
    assert 'getenv("LLAMA_MOE_RECORD_DIR")' in cache
    # the directory is the only door: the fill and the fold both stand behind it
    assert re.search(r"if \(!record_dir\.empty\(\)\) \{\s+fill_from_record\(\);", cache)
    assert re.search(r"if \(!record_dir\.empty\(\)\) \{\s+fold_into_record\(\);", cache)
    assert cache.count("fill_from_record();") == 1 and cache.count("fold_into_record();") == 1
    assert "`LLAMA_MOE_RECORD_DIR`" in NOTE and "unset: nothing is read, kept or filled" in NOTE


def test_the_share_of_the_cache_the_fill_takes_is_the_model_s_env_too():
    """Added after the node's traces (the note's §6): a whole fill uploads the whole cache inside the load, and the
    card must be able to measure a part of it on the same engine."""
    cache = "\n".join(_added(_files()["src/llama-moe-cache.cpp"]))
    # read only where a record is kept; what is not a share is said and the whole cache is filled, as without it
    assert re.search(r'getenv\("LLAMA_MOE_RECORD_FILL"\); fill != nullptr && \*fill != \'\\0\' && !record_dir\.empty\(\)', cache)
    assert "double record_fill = 1.0;" in cache
    assert "if (!llama_moe_record::parse_fill(fill, record_fill)) {" in cache and "is not a share from 0 to 1" in cache
    # it only shortens the list of experts the fill takes: the best-ranked first, as before
    assert "record.best(g.layers, llama_moe_record::fill_slots(record_fill, (size_t) g.n_slots));" in cache
    record = _new_file(_files()["src/llama-moe-record.h"])
    assert "v < 0.0 || v > 1.0" in record and "std::floor(share*(double) n_slots)" in record
    assert "`LLAMA_MOE_RECORD_FILL`" in NOTE and "| D, the fill at a half and at a quarter |" in NOTE


def test_the_fill_is_in_the_cache_s_creation_and_the_file_s_write_in_its_destruction():
    """The architecture session's conditions: the fill belongs to the load, never to a person's first token; the
    file is written off the token path, whole or not at all."""
    diff = _files()["src/llama-moe-cache.cpp"]
    cache = "\n".join(_added(diff))
    # the fold is called from the destructor, the fill from the end of the constructor
    # (the destructor is the one place that closes the trace file: the fold follows it)
    assert re.search(r"^ +if \(trace != nullptr\) \{\n +fclose\(trace\);\n +\}\n\+ +if \(!record_dir\.empty\(\)\) \{\n"
                     r"\+ +fold_into_record\(\);", diff, re.M)
    assert "ggml_backend_synchronize(backend);" in cache, "the fill is done when the constructor returns"
    assert "put in the cache from the record of" in cache and "MiB uploaded in %.1f ms" in cache, "and says its size and its time"
    # copy(), the token path, writes no file and folds nothing: the only save is in the fold
    assert cache.count(".save(") == 1 and "store.record.save(record_path(id))" in cache
    record = _new_file(_files()["src/llama-moe-record.h"])
    assert 'const std::string tmp = path + ".tmp";' in record and "rename(tmp.c_str(), path.c_str())" in record
    # the eviction order is seeded from the same record, in the cache's own units
    assert "void seed(int32_t il, int32_t e, int32_t s, float crf_seed)" in cache
    assert "moe_cache_lru::crf_half_life/llama_moe_record::half_life" in cache
    # what the record is of: these weights and this build
    assert 'id.build      = __DATE__ " " __TIME__;' in cache
    for said in ("| THE FILL BELONGS TO THE LOAD |", "| the card bytes at rest | UNCHANGED", "| counts, no text; on the node only |"):
        assert said in NOTE, said


@pytest.mark.skipif(shutil.which("g++") is None, reason="no C++ compiler here")
def test_the_record_s_own_test_passes_built_from_the_patch(tmp_path):
    files = _files()
    (tmp_path / "llama-moe-record.h").write_text(_new_file(files["src/llama-moe-record.h"]), encoding="utf-8")
    (tmp_path / "test-moe-record.cpp").write_text(_new_file(files["tests/test-moe-record.cpp"]), encoding="utf-8")
    built = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", f"-I{tmp_path}",
                            str(tmp_path / "test-moe-record.cpp"), "-o", str(tmp_path / "t")],
                           capture_output=True, text=True, timeout=180)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, timeout=120)
    assert ran.returncode == 0, ran.stderr
    for said in ("ok:   a record cut short at any byte is unreadable", "ok:   a record with any byte changed is not loaded",
                 "ok:   the record of other weights is not used", "ok:   nor the record of another build of the engine",
                 "ok:   a tie is broken the same way every time",
                 "ok:   what is not a share from 0 to 1 is refused and changes nothing",
                 "ok:   a share of the slots, rounded down", "ok:   half a cache takes the better half of the record"):
        assert said in ran.stderr, said


def test_the_note_says_what_ran_and_what_the_node_measures():
    assert "the fill itself has\nNOT run" in NOTE
    tested = NOTE[NOTE.index("## 3. "):NOTE.index("## 4. ")]
    assert "| the fill and the seeded eviction | NOT RUN." in tested and "35 checks" in tested
    node = NOTE[NOTE.index("## 5. "):]
    for run in ("| A, today |", "| B1, B2, a second context of the same process |", "| C, a new process reading the file |",
                "| the proof | A, B1, B2, C, D | the answers equal"):
        assert run in node, run
    # the record that is measured was built from OTHER questions, and the fill is counted once
    assert "the record C and D read does not hold the answer they measure" in node
    assert "A, C and D run on the live 35B's own context options" in node
    assert "THE LOAD'S SECONDS against A's" in node, "the fill moved into the load: its cost is read there"
    assert "staged/" + PATCH.name in (STAGED / "engine-next.series").read_text(encoding="utf-8")
    assert PATCH.name not in (PATCHES / "series").read_text(encoding="utf-8")
