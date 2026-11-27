"""NO HARD LINKS (operator, 2026-11-27, verbatim: "Never use hard links,
always consciously load from a path. It already happened that you deleted
important files because you forgot the hard links"; app/file_copy.py).

Nothing in app/, tools/ or deploy/ makes a second name for a file: no
`os.link`, `Path.hardlink_to`/`link_to`, `copy_function=os.link`, `cp -l`/
`cp -al`/`cp --link`, or `ln` without `-s`. A symbolic link names its
target's path and is allowed."""
from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRS = ("app", "tools", "deploy")


def _files(suffix: str) -> list[Path]:
    return sorted(p for d in DIRS for p in (ROOT / d).rglob(f"*{suffix}")
                  if "__pycache__" not in p.parts)


def _is_os_link(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "link"
            and isinstance(node.value, ast.Name) and node.value.id == "os")


def _cp_links(args: list[str]) -> bool:
    flags = [a for a in args[1:] if a.startswith("-")]
    return any(a == "--link" or (not a.startswith("--") and "l" in a[1:]) for a in flags)


def _ln_hard(args: list[str]) -> bool:
    flags = [a for a in args[1:] if a.startswith("-")]
    return not any(a == "--symbolic" or (not a.startswith("--") and "s" in a[1:]) for a in flags)


def _command_links(words: list[str]) -> bool:
    if not words:
        return False
    head = Path(words[0]).name
    return (head == "cp" and _cp_links(words)) or (head == "ln" and _ln_hard(words))


def python_hard_links(source: str) -> list[int]:
    """The lines of `source` that make a hard link."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            f = node.func
            if _is_os_link(f) or (isinstance(f, ast.Attribute) and f.attr in ("hardlink_to", "link_to")):
                out.append(node.lineno)
            for kw in node.keywords:
                if kw.arg == "copy_function" and _is_os_link(kw.value):
                    out.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module == "os" \
                and any(a.name == "link" for a in node.names):
            out.append(node.lineno)
        elif isinstance(node, (ast.List, ast.Tuple)):
            words = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if len(words) == len(node.elts) and _command_links(words):
                out.append(node.lineno)
    return sorted(set(out))


_SPLIT = re.compile(r"[;&|]+|\$\(|`")


def shell_hard_links(text: str) -> list[int]:
    """The lines of a shell script that run `ln` without `-s`, or `cp` with
    a link flag."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0] if not line.lstrip().startswith("#!") else ""
        for part in _SPLIT.split(code):
            try:
                words = shlex.split(part)
            except ValueError:
                words = part.split()
            while words and ("=" in words[0] or words[0] in ("sudo", "exec", "command", "then", "do",
                                                               "else", "!", "(", "{")):
                words = words[1:]
            if _command_links(words):
                out.append(i)
    return out


def test_no_hard_link_is_made_in_python():
    found = {str(p.relative_to(ROOT)): lines for p in _files(".py")
             if (lines := python_hard_links(p.read_text(encoding="utf-8")))}
    assert not found, f"hard links (operator, 2026-11-27: never; app/file_copy.py): {found}"


def test_no_hard_link_is_made_in_shell():
    found = {str(p.relative_to(ROOT)): lines for p in _files(".sh")
             if (lines := shell_hard_links(p.read_text(encoding="utf-8", errors="replace")))}
    assert not found, f"hard links (operator, 2026-11-27: never; app/file_copy.py): {found}"


def test_the_guard_finds_what_it_is_for():
    assert python_hard_links("import os\nos.link('a', 'b')\n") == [2]
    assert python_hard_links("from pathlib import Path\nPath('b').hardlink_to('a')\n") == [2]
    assert python_hard_links("from os import link\n") == [1]
    assert python_hard_links("import os, shutil\nshutil.copytree('a', 'b', copy_function=os.link)\n") == [2]
    assert python_hard_links("import subprocess\nsubprocess.run(['cp', '-al', 'a', 'b'])\n") == [2]
    assert python_hard_links("import subprocess\nsubprocess.run(['ln', 'a', 'b'])\n") == [2]
    # what is allowed: a symbolic link, a reflink copy, `ln` the variable
    assert python_hard_links("from pathlib import Path\nPath('b').symlink_to('a')\n") == []
    assert python_hard_links("import subprocess\nsubprocess.run(['cp', '-a', '--reflink=auto', 'a', 'b'])\n") == []
    assert python_hard_links("import subprocess\nsubprocess.run(['ln', '-sfn', 'a', 'b'])\n") == []
    assert python_hard_links("lines = [ln for ln in x if ln]\n") == []
    assert shell_hard_links("ln a b\n") == [1]
    assert shell_hard_links("cp -al a b\ncp --link a b\n") == [1, 2]
    assert shell_hard_links("ln -s a b\nln -sfn \"$a\" b\ncp -a --reflink=auto a b\n# ln a b\n") == []
    assert shell_hard_links("x=1 && ln a b\n") == [1]
