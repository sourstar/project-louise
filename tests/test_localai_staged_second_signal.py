"""The staged backend fix `0014-llama-cpp-second-signal-exit.patch`: a second
termination signal leaves at once instead of calling exit() inside the
handler (docs/notes/localai-bugs.md B19 / P15).

Found 2026-11-24: 3 of 14 unloads of a model behind the vega wrapper took
LocalAI's whole 15 s. LocalAI stops a backend with SIGTERM to its process
group, the wrapper passed the signal on, and llama.cpp's handler answers a
second one with exit(1), which can block against the clean-up it interrupts.
The patch carries its own standalone test; it is built and run here, from
the patch, with the flags of LocalAI's unit-test runner."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAGED = ROOT / "deploy" / "localai" / "patches" / "staged"
PATCH = STAGED / "0014-llama-cpp-second-signal-exit.patch"
ACTIONS = (ROOT / "docs" / "notes" / "localai-bugs.md").read_text(encoding="utf-8")
WRAPPER = (ROOT / "deploy" / "vega" / "vega_wrapper.py").read_text(encoding="utf-8")
DIR = "backend/cpp/llama-cpp/"


def _files() -> dict[str, str]:
    """{path: the patch's diff for it}."""
    text = PATCH.read_text(encoding="utf-8")
    parts = re.split(r"^diff --git a/(\S+) b/\S+\n", text[text.index("\n---\n"):], flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def _new_file(diff: str) -> str:
    assert "new file mode" in diff
    body = diff[diff.index("\n@@ "):].split("\n", 2)[2]
    lines = [line[1:] for line in body.splitlines() if line.startswith("+")]
    return "\n".join(lines) + "\n"


def test_it_is_a_mail_under_localai_s_policy():
    text = PATCH.read_text(encoding="utf-8")
    assert text.startswith("From 0000000000000000000000000000000000000000 ")
    message = text[:text.index("\n---\n")]
    assert re.search(r"^Subject: \[PATCH\] fix\(llama-cpp\): ", message, re.M)
    assert re.search(r"^Assisted-by: ", message, re.M)
    assert not re.search(r"^(Signed-off-by|Co-Authored-By):", message, re.M | re.I)


def test_the_handler_no_longer_calls_exit_and_the_header_reaches_the_build():
    files = _files()
    assert set(files) == {DIR + name for name in (
        "CMakeLists.txt", "grpc-server.cpp", "prepare.sh", "second_signal.h", "second_signal_test.cpp")}
    main = files[DIR + "grpc-server.cpp"]
    removed = [line[1:].strip() for line in main.splitlines() if line.startswith("-") and not line.startswith("---")]
    added = [line[1:].strip() for line in main.splitlines() if line.startswith("+") and not line.startswith("+++")]
    assert "exit(1);" in removed and any(line.startswith("fprintf(stderr,") for line in removed)
    assert "llama_grpc::terminate_on_second_signal();" in added
    assert any(line.startswith('#include "second_signal.h"') for line in added)
    # LocalAI's build copies each header beside llama.cpp's sources: one left out is a build that fails
    assert "+cp -r second_signal.h llama.cpp/tools/grpc-server/" in files[DIR + "prepare.sh"]
    assert "add_test(NAME second_signal_test COMMAND second_signal_test)" in files[DIR + "CMakeLists.txt"]
    header = _new_file(files[DIR + "second_signal.h"])
    body = header[header.index("inline void terminate_on_second_signal()"):]
    assert "std::_Exit(1);" in body and "write(STDERR_FILENO, msg, sizeof(msg) - 1)" in body
    assert not re.search(r"\b(exit|fprintf|printf|puts)\(", body), "async-signal-safe calls only"
    assert '"Received second interrupt, terminating immediately.\\n"' in body, "the message is unchanged"


@pytest.mark.skipif(shutil.which("g++") is None, reason="no C++ compiler here")
def test_its_own_test_passes_built_as_localai_s_runner_builds_it(tmp_path):
    files = _files()
    for name in ("second_signal.h", "second_signal_test.cpp"):
        (tmp_path / name).write_text(_new_file(files[DIR + name]), encoding="utf-8")
    built = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pthread", f"-I{tmp_path}",
                            str(tmp_path / "second_signal_test.cpp"), "-o", str(tmp_path / "t")],
                           capture_output=True, text=True, timeout=120)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stderr
    assert "ok:   the process is gone although an exit handler never returns" in ran.stderr
    assert "ok:   exit() from the handler is still there a second later" in ran.stderr


def test_the_upstream_list_names_the_defect_its_fix_and_what_our_wrapper_leaves_open():
    assert "defects found (B1–B19)" in ACTIONS and "| B19 | defect |" in ACTIONS
    assert "### B19 and P15 (staged). " in ACTIONS
    row = re.search(r"^\| \*\*P15 \(staged\)\*\* \|.*$", ACTIONS, re.M)
    assert row and PATCH.name in row.group(0) and "to file upstream" in row.group(0)
    assert PATCH.name in (STAGED / "README.md").read_text(encoding="utf-8")
    # the section's two numbers are the wrapper's own
    section = ACTIONS[ACTIONS.index("### B19 and P15 (staged). "):]
    section = section[:section.index("\n### ", 10)]
    assert re.search(r"^OWN_EXIT_S = 2\.0$", WRAPPER, re.M) and "`OWN_EXIT_S` = 2 s" in section
    assert re.search(r"^GRACE_S = 12\.0$", WRAPPER, re.M) and "over in 12 s, under LocalAI's 15" in section
