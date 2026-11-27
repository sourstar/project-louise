"""Staged 0037: our llama.cpp on upstream's latest (the operator, 2026-11-27:
"1.B always"; docs/notes/llama-cpp-upstream.md).

Held here: the pin and what the outer patch touches; LocalAI's own fix for the
decision API (#32578) with its probe; the nine inner patches as the series
leaves them, rebuilt from the series' own outer patches - the OmniVoice
backbone without the scale arguments upstream removed (#28898), the BERT
pair patch without its own pooler hunk (upstream's #33122) and with the GELU
key for upstream's sequence classifier too; the candidate and its name; what
the note says was and was not measured."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "deploy" / "localai" / "patches"
STAGED = PATCHES / "staged"
PATCH = STAGED / "0037-llama-cpp-upstream-8e23278c.patch"
SERIES = STAGED / "engine-upstream.series"
BASE = STAGED / "engine-guard.series"
BUILD = ROOT / "deploy" / "localai" / "llama-cpp" / "build.sh"
NOTE = (ROOT / "docs" / "notes" / "llama-cpp-upstream.md").read_text(encoding="utf-8")
NAME = "1f6cedc4307a"
PIN = "8e23278c7c36f4429fb237467d50e2935a91e655"
B = "backend/cpp/llama-cpp/"
CARRIED = ["0008-chat-cohere-command3-parser.patch", "0012-mtmd-omnivoice.patch", "0013-sycl-reorder-experts-switch.patch",
           "0015-park-weights.patch", "0016-sycl-pool-refuse-switch.patch", "0017-moe-record.patch",
           "0022-sycl-graph-refuse-switch.patch", "0023-server-park-when-told.patch", "0026-bert-text-pairs.patch"]

OUTER = PATCH.read_text(encoding="utf-8")


def _sections(patch: str) -> dict[str, str]:
    parts = re.split(r"(?m)^(?=diff --git a/)", patch[patch.index("\n---\n"):])
    return {re.match(r"diff --git a/(\S+)", p).group(1): p for p in parts if p.startswith("diff --git")}


def _added(section: str) -> str:
    return "\n".join(line[1:] for line in section.splitlines() if line.startswith("+") and not line.startswith("+++ "))


FILES = _sections(OUTER)


def _series(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#") and not line.startswith("base ")]


def test_it_is_a_mail_under_localai_s_policy_and_touches_what_it_says():
    assert OUTER.startswith("From 0000000000000000000000000000000000000000 ")
    message = OUTER[:OUTER.index("\n---\n")]
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)
    assert sorted(FILES) == sorted([B + "Makefile", B + "grpc-server.cpp", B + "prepare.sh"]
                                   + [B + "patches/" + p for p in CARRIED])


def test_the_pin_is_upstream_master_of_the_day():
    makefile = FILES[B + "Makefile"]
    assert f"+LLAMA_VERSION?={PIN}" in makefile
    assert "-LLAMA_VERSION?=78e05298f0d68abff41d08d79fa7469264b2aff0" in makefile


def test_the_decision_api_takes_localai_s_own_fix_with_its_probe():
    server = _added(FILES[B + "grpc-server.cpp"])
    assert "#if LOCALAI_HAS_DECISION_AUDIO_INPUT" in server
    assert "decision.parse_questions(body, ctx_server.impl->chat_params.allow_audio);" in server
    prepare = _added(FILES[B + "prepare.sh"])
    assert 'grep -q "bool support_audio_input" llama.cpp/tools/server/server-decision.h' in prepare
    assert "#define LOCALAI_HAS_DECISION_AUDIO_INPUT ${HAS_DECISION_AUDIO_INPUT}" in prepare


@pytest.fixture(scope="module")
def inner(tmp_path_factory) -> dict[str, str]:
    """The nine inner patches as the series leaves them: each one's creating outer patch, then 0037."""
    if shutil.which("git") is None:
        pytest.skip("no git here")
    tree = tmp_path_factory.mktemp("inner")
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    wanted = [B + "patches/" + p for p in CARRIED]
    for name in _series(SERIES):
        text = (PATCHES / name).read_text(encoding="utf-8")
        touched = set(re.findall(r"^diff --git a/(\S+)", text, re.M)) & set(wanted)
        if not touched:
            continue
        done = subprocess.run(["git", "apply", *(f"--include={w}" for w in sorted(touched)), str(PATCHES / name)],
                              cwd=tree, capture_output=True, text=True)
        assert done.returncode == 0, (name, done.stderr)
    return {p: (tree / B / "patches" / p).read_text(encoding="utf-8") for p in CARRIED}


def _inner_added(patch: str, path: str) -> str:
    parts = re.split(r"(?m)^(?=diff --git a/)", patch)
    section = next(p for p in parts if p.startswith(f"diff --git a/{path} "))
    return _added(section)


def test_the_omnivoice_backbone_has_no_scale_arguments_left(inner):
    graph = _inner_added(inner["0012-mtmd-omnivoice.patch"], "src/models/omnivoice.cpp")
    for gone in ("wo_s", "ffn_up_s", "ffn_gate_s", "ffn_down_s", "output_s"):
        assert gone not in graph, gone
    assert "model.layers[il].wo, model.layers[il].wo_b,\n" in graph
    assert "cur = build_lora_mm(model.output, cur);" in graph


def test_the_pair_patch_leaves_the_pooler_to_upstream_and_keeps_the_gelu_for_its_classifier(inner):
    pair = inner["0026-bert-text-pairs.patch"]
    converter = _inner_added(pair, "conversion/bert.py")
    assert "pooler" not in converter                                      # upstream's #33122 keeps it
    assert ('if type(self) in (BertModel, BertForSequenceClassification) and '
            '(hidden_act := self.hparams.get("hidden_act")):') in converter
    assert "self.gguf_writer.add_hidden_act(hidden_act)" in converter
    message = pair[:pair.index("\ndiff --git ")]
    assert "ggml-org/llama.cpp#33122" in message and "pooler" not in message.splitlines()[0]
    # the FFN in upstream's new call form, with the declared activation
    bert = _inner_added(pair, "src/models/bert.cpp")
    assert "ffn_op, LLM_FFN_SEQ, il);" in bert


def test_the_candidate_is_the_guard_engine_plus_this_patch_and_its_name_is_held():
    assert _series(SERIES) == _series(BASE) + ["staged/" + PATCH.name]
    base = lambda p: [l for l in p.read_text(encoding="utf-8").splitlines() if l.startswith("base ")]
    assert base(SERIES) == base(BASE)                                     # LocalAI's base does not move
    done = subprocess.run(["sh", str(BUILD), "sycl_f16", "/nonexistent"], capture_output=True, text=True,
                          env=dict(os.environ, SERIES_FILE=str(SERIES), NAME_ONLY="1"), cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == NAME
    readme = (STAGED / "README.md").read_text(encoding="utf-8")
    assert f"**A candidate on upstream llama.cpp's latest: `engine-upstream.series`** (`{NAME}`;" in readme


def test_the_note_says_the_rule_what_was_measured_and_what_was_not():
    note = " ".join(NOTE.split())
    for said in ('"1.B always"', "Every new engine starts from upstream llama.cpp's master",
                 "LocalAI's base stays where it is", "with `patch -p1` and NO fuzz",
                 "MedCPT's article encoder, 67 PubMed pairs", "the same bytes",
                 "`62d0e7b9bd70ec2155c641d5fa858d66a3a6a8bcd689b36a04254a7a4cadd7f5`, THE SAME BYTES",
                 "`4dff2f7712595ad1c69d3d62c9f1a40d56cf0375aa072b2e53708d0c2ea4ec50`",
                 "25 of 25", "#32726", "Every gate again on the card"):
        assert said in note, said
