#!/usr/bin/env python3
"""A stream that fails after its first chunk must END AS A FAILURE
(docs/notes/localai-bugs.md B21; staged 0025).

LocalAI's llama.cpp backend tested only the FIRST result of a PredictStream for
an error. A later one - a computation failing in the middle of a generation -
became one more, empty, reply and the call returned OK: an answer cut short,
delivered as complete.

Where there is no card: LocalAI's own grpc-server, built for CPU with the
stand-in that refuses a graph once a file exists
(tools/measure/refuse_cpu_standin.patch, in no engine):

    python3 -I tools/measure/stream_fails_test.py <grpc-server> <a small model that generates>.gguf

It streams one prompt whole, then the same prompt with a graph refused when
the first chunk is in, then the prompt again. Exit 0: the second stream ended
as an error and the third is whole. Exit 1 says what it saw - "DEFECT" when the
second stream ended OK with fewer chunks than the first.

ON A CARD, on a COPY of a chat model whose env has GGML_SYCL_GRAPH_REFUSE=1 and
GGML_SYCL_GRAPH_REFUSE_ARM=<file> (staged 0026): against the backend's own
address,

    python3 -I tools/measure/stream_fails_test.py --addr ADDR --arm <file> [--tokens 200]

- the model is the one LocalAI loaded there; the file must not exist when it
starts, and is removed at the end.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("park_told", HERE / "park_told.py")
park_told = importlib.util.module_from_spec(spec)
spec.loader.exec_module(park_told)

PROMPT = "Once upon a time"


def verdict(whole: tuple[str, int, str], cut: tuple[str, int, str], after: tuple[str, int, str]) -> tuple[bool, list[str]]:
    """what the three streams say: (passed, the lines to print)"""
    lines = [f"the whole answer:        {whole[0]}, {whole[1]} chunks {whole[2]}".rstrip(),
             f"a graph refused mid-way: {cut[0]}, {cut[1]} chunks {cut[2]}".rstrip(),
             f"the next answer:         {after[0]}, {after[1]} chunks {after[2]}".rstrip()]
    if whole[0] != "OK" or whole[1] < 4:
        return False, lines + ["NOT RUN: the model does not stream an answer of several chunks"]
    if cut[0] == "OK" and cut[1] >= whole[1]:
        return False, lines + ["NOT RUN: no graph was refused - the second stream is as long as the first"]
    if cut[0] == "OK":
        return False, lines + [f"DEFECT: the stream ended OK after {cut[1]} of {whole[1]} chunks - "
                               "an answer cut short by a failure, delivered as complete"]
    if after[0] != "OK" or after[1] != whole[1]:
        return False, lines + ["FAIL: the stream after the failure is not the whole answer again"]
    return True, lines + [f"ok: the stream ended as a failure ({cut[0]}) after {cut[1]} of {whole[1]} chunks, "
                          "and the next one is whole"]


def run(backend, arm: Path, tokens: int) -> tuple[bool, list[str]]:
    arm.unlink(missing_ok=True)
    whole = backend.stream(PROMPT, tokens)
    cut = backend.stream(PROMPT, tokens, on_first=lambda: arm.write_text("armed"))
    arm.unlink(missing_ok=True)
    after = backend.stream(PROMPT, tokens)
    return verdict(whole, cut, after)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("binary", nargs="?", help="LocalAI's grpc-server, built with the CPU stand-in")
    ap.add_argument("gguf", nargs="?", help="a small model that generates")
    ap.add_argument("--addr", help="a running backend's own address, instead of starting one")
    ap.add_argument("--arm", help="with --addr: the file its GGML_SYCL_GRAPH_REFUSE_ARM names")
    ap.add_argument("--tokens", type=int, default=48)
    ap.add_argument("--stubs", help="a directory that holds backend_pb2.py and backend_pb2_grpc.py")
    ap.add_argument("--token-file", help="the file that holds the backend's token")
    a = ap.parse_args()

    if a.addr:
        if not a.arm:
            raise SystemExit("--addr needs --arm: the file the backend's GGML_SYCL_GRAPH_REFUSE_ARM names")
        passed, lines = run(park_told.Backend(a.addr, a.stubs, a.token_file), Path(a.arm), a.tokens)
    else:
        if not (a.binary and a.gguf):
            raise SystemExit("give <grpc-server> <model.gguf>, or --addr ADDR --arm FILE")
        arm = Path(tempfile.mkdtemp(prefix="stream-arm-")) / "arm"
        p = park_told.Process(a.binary, {"GGML_CPU_REFUSE_GRAPHS": "1", "GGML_CPU_REFUSE_ARM": str(arm)}, a.stubs)
        try:
            code, answer, detail, _ = p.backend.load("stream-fails-test", ModelFile=a.gguf, ContextSize=256, NBatch=64,
                                                     Threads=4, MMap=False, Options=["verbosity:4"])
            if code != "OK" or not answer.success:
                raise SystemExit(f"the model does not load: {code} {detail}")
            passed, lines = run(p.backend, arm, a.tokens)
            if passed and p.proc.poll() is not None:
                passed, lines = False, lines + ["FAIL: the backend process is gone"]
        finally:
            p.stop()
    print("\n".join(lines))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
