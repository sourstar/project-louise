"""Staged 0034: MedCPT on our llama.cpp - a BERT text pair for embedding and
rerank (docs/notes/medcpt-on-llama-cpp.md; the operator, 2026-11-26: "Move
both").

Held here: what each half changes and where; that without `pairs:true` the
graph, the prompt and the score are what they were; the pair built and cut as
Hugging Face does (the patch's own test, compiled and run from the patch,
with Hugging Face's table) and its segments (the inner patch's test, likewise);
the marker the very one VeGa writes; a fork without the segments refusing
the option; the converter keeping a classifier's pooler; the candidate and its
name; what the note says was and was not measured."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.vega import tag as vtag

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"
PATCH = STAGED / "0034-llama-cpp-bert-text-pairs.patch"
SERIES = STAGED / "engine-pair.series"
BASE = STAGED / "engine-pace.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
NOTE = (ROOT / "docs" / "notes" / "medcpt-on-llama-cpp.md").read_text(encoding="utf-8")
NAME = "b77c50dd0fbb"
B = "backend/cpp/llama-cpp/"
INNER = B + "patches/0026-bert-text-pairs.patch"


def _sections(patch: str) -> dict[str, str]:
    """Each file's part of a patch, by its path."""
    parts = re.split(r"(?m)^(?=diff --git a/)", patch[patch.index("\n---\n"):])
    return {re.match(r"diff --git a/(\S+)", p).group(1): p for p in parts if p.startswith("diff --git")}


def _added(section: str) -> str:
    return "\n".join(line[1:] for line in section.splitlines() if line.startswith("+") and not line.startswith("+++ "))


def _removed(section: str) -> str:
    return "\n".join(line[1:] for line in section.splitlines() if line.startswith("-") and not line.startswith("--- "))


OUTER = PATCH.read_text(encoding="utf-8")
FILES = _sections(OUTER)
INNER_TEXT = "\n".join(line[1:] for line in FILES[INNER].splitlines()
                       if line.startswith("+") and not line.startswith("+++ b/backend/")) + "\n"
INNER_FILES = _sections(INNER_TEXT)
SERVER = _added(FILES[B + "grpc-server.cpp"])


def test_it_is_a_mail_under_localai_s_policy_and_touches_what_it_says():
    assert OUTER.startswith("From 0000000000000000000000000000000000000000 ")
    message = OUTER[:OUTER.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert sorted(FILES) == sorted(B + f for f in ("CMakeLists.txt", "grpc-server.cpp", "patches/0026-bert-text-pairs.patch",
                                                    "prepare.sh", "text_pair.h", "text_pair_test.cpp"))
    assert sorted(INNER_FILES) == sorted([
        "common/common.cpp", "common/common.h", "conversion/base.py", "conversion/bert.py", "convert_hf_to_gguf_update.py",
        "include/llama.h", "src/llama-context.cpp", "src/llama-cparams.h", "src/llama-pair-segments.h",
        "src/models/bert.cpp", "tests/test-bert-pair-segments.cpp"])
    assert "text_pair_test" in _added(FILES[B + "CMakeLists.txt"])
    assert "cp -r text_pair.h llama.cpp/tools/grpc-server/" in _added(FILES[B + "prepare.sh"])


def test_the_graph_reads_segments_only_when_the_context_asks():
    bert = INNER_FILES["src/models/bert.cpp"]
    added, removed = _added(bert), _removed(bert)
    assert "if (model.type_embd && cparams.pair_segments && ubatch.token) {" in added
    assert "inpL = ggml_add(ctx0, inpL, ggml_get_rows(ctx0, model.type_embd, inp->segments));" in added
    # without it the original lines: row 0 for every token, the same ops
    assert "ggml_tensor * type_row0 = ggml_view_1d(ctx0, model.type_embd, n_embd, 0);" not in removed
    assert "inpL                    = ggml_add(ctx0, inpL, type_row0);" not in removed
    assert "} else if (model.type_embd) {" in added
    context = _added(INNER_FILES["src/llama-context.cpp"])
    for refusal in ("only the BERT architecture reads a text pair's segments", "the model has no second token-type row",
                    "the model's vocabulary has no separator token"):
        assert refusal in context, refusal
    assert "/*.pair_segments               =*/ false," in context


def test_the_server_reads_pairs_only_under_its_option_and_a_fork_refuses_it():
    assert '} else if (!strcmp(optname, "pairs")) {' in SERVER
    assert '} else if (!strcmp(optname, "rerank_score")) {' in SERVER
    # each path behind the option
    assert SERVER.count("pair_segments_of(params_base)") == 3
    assert "tmp = format_prompt_rerank(" in SERVER            # the rerank as before, without it
    assert "if (rerank_score_sigmoid) {" in SERVER and "doc_result->set_relevance_score(score);" in SERVER
    # a llama.cpp without the segments: the load refused, never a pair read as one segment
    assert "if (pairs_asked && !LOCALAI_HAS_PAIR_SEGMENTS) {" in SERVER
    assert "static bool pair_segments_of(const common_params &) { return false; }" in SERVER
    assert '#define LOCALAI_HAS_PAIR_SEGMENTS ${HAS_PAIR_SEGMENTS}' in _added(FILES[B + "prepare.sh"])
    # the tokenizer reads special tokens in the text as Hugging Face's does
    assert "common_tokenize(vocab, a, /*add_special=*/false, /*parse_special=*/true)" in SERVER


def test_the_marker_is_the_one_vega_writes():
    header = _added(FILES[B + "text_pair.h"])
    assert 'inline const std::string PAIR_MARK = "\\x1e" "LOUISE-PAIR" "\\x1e";' in header
    assert vtag.PAIR_MARK == "\x1e" + "LOUISE-PAIR" + "\x1e"


def test_the_gelu_is_the_one_the_file_declares_and_none_keeps_the_tanh():
    bert = INNER_FILES["src/models/bert.cpp"]
    added = _added(bert)
    assert "hparams.llm_ffn_op = LLM_FFN_GELU;" in added                       # no key: the tanh GELU, as before
    assert 'if (hidden_act == "gelu" || hidden_act == "gelu_python") {\n            hparams.llm_ffn_op = LLM_FFN_GELU_ERF;' in added
    assert "is not one this graph computes" in added                           # an unknown activation refused
    assert "const llm_ffn_op_type ffn_op = model.arch == LLM_ARCH_BERT ? hparams.llm_ffn_op : LLM_FFN_GELU;" in added
    assert "LLM_FFN_GELU, LLM_FFN_SEQ, il);" in _removed(bert) and "ffn_op, LLM_FFN_SEQ, il);" in added
    # the standard key (ModernBert's), written by the converter for BertModel itself
    converter = _added(INNER_FILES["conversion/bert.py"])
    assert 'if type(self) is BertModel and (hidden_act := self.hparams.get("hidden_act")):' in converter
    assert "self.gguf_writer.add_hidden_act(hidden_act)" in converter


def test_the_converter_keeps_a_classifier_s_pooler_and_knows_medcpt():
    bert = _added(INNER_FILES["conversion/bert.py"])
    assert 'if name in ("pooler.dense.weight", "pooler.dense.bias") and cls is not BertModel:' in bert
    assert 'name = name.replace("pooler.dense", "classifier.dense")' in bert
    assert "if not self.cls_out_labels:" in bert
    assert 'res = "medcpt"' in _added(INNER_FILES["conversion/base.py"])
    assert '"name": "medcpt",           "tokt": TOKENIZER_TYPE.WPM' in _added(INNER_FILES["convert_hf_to_gguf_update.py"])


def _tree(tmp_path: Path, text: str, includes: list[str]) -> Path:
    (tmp_path / "p.patch").write_text(text, encoding="utf-8")
    tree = tmp_path / "tree"
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    done = subprocess.run(["git", "apply", *(f"--include={i}" for i in includes), str(tmp_path / "p.patch")],
                          cwd=tree, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return tree


def _run(tmp_path: Path, include: Path, source: Path) -> list[str]:
    built = subprocess.run(["c++", "-std=c++17", "-w", "-I", str(include), str(source), "-o", str(tmp_path / "t")],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr[-2000:]
    ran = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stdout
    return ran.stdout.splitlines()


@pytest.mark.skipif(shutil.which("c++") is None or shutil.which("git") is None, reason="no C++ compiler or git here")
def test_the_pair_is_built_and_cut_as_hugging_face_does(tmp_path):
    tree = _tree(tmp_path, OUTER[OUTER.index("\n---\n") + 5:], [B + "text_pair.h", B + "text_pair_test.cpp"])
    lines = _run(tmp_path, tree / B, tree / B / "text_pair_test.cpp")
    assert lines[-1] == "all passed" and sum(line.startswith("ok:") for line in lines) == 15
    assert "ok:   longest first keeps what Hugging Face keeps: 216 of 216 rows" in lines


@pytest.mark.skipif(shutil.which("c++") is None or shutil.which("git") is None, reason="no C++ compiler or git here")
def test_the_segments_rule_passes_from_the_inner_patch(tmp_path):
    tree = _tree(tmp_path, INNER_TEXT, ["src/llama-pair-segments.h", "tests/test-bert-pair-segments.cpp"])
    lines = _run(tmp_path, tree / "src", tree / "tests" / "test-bert-pair-segments.cpp")
    assert lines[-1] == "all passed" and sum(line.startswith("ok:") for line in lines) == 8


def test_the_candidate_is_the_pace_engine_plus_this_patch_and_its_name_is_held():
    lines = lambda p: [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    assert lines(SERIES) == lines(BASE) + ["staged/" + PATCH.name]
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(SERIES), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == NAME
    readme = (STAGED / "README.md").read_text(encoding="utf-8")
    assert f"**A candidate after the pace one, MedCPT on our llama.cpp: `engine-pair.series`** (`{NAME}`;" in readme


def test_the_note_says_what_was_measured_and_what_was_not():
    note = " ".join(NOTE.split())                 # the note's words, whatever its line breaks
    for said in (NAME, "**0.9999999**", "0.9999882", "0.9736", "**within 0.00063**", "216 of 216",
                 "## 4. The article encoder is a NEW pubmed space", "**its own CPU reference**, mandatory",
                 "**Why the GELU is decided now.**", "67 of 67 vectors and 80 of 80 logits the same floats",
                 "**Batching is the speed-up, and it breaks the score's independence from its batch-mates**",
                 "| 4 a pass (`parallel:4`, ubatch 2,048), FA ON | 2.7 s | 174 of 192 |",
                 "NOT measured: on the card", "`cls.weight` and `cls.bias`", '`bert.hidden_activation = "gelu"`',
                 "A model that fails stays on OpenVINO."):
        assert said in note, said
