"""NO HARD LINKS (operator, 2026-11-27, verbatim: "Never use hard links,
always consciously load from a path. It already happened that you deleted
important files because you forgot the hard links"; app/file_copy.py).

Nothing in app/, tools/ or deploy/ makes a second name for a file: no
`os.link`, `Path.hardlink_to`/`link_to`, `copy_function=os.link`, `cp -l`/
`cp -al`/`cp --link`, or `ln` without `-s`. A symbolic link names its
target's path and is allowed; a sister's seeded SHARD parts are neither: her
own copy (operator, 2026-11-27, choice B; tests/hold/test_carry.py)."""
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


def _is_os_link(node: ast.AST, os_names: frozenset = frozenset({"os"}),
                link_names: frozenset = frozenset()) -> bool:
    """`os.link` under any name the module bound it to (`import os as o`,
    `from os import link as l`)."""
    if isinstance(node, ast.Name):
        return node.id in link_names
    return (isinstance(node, ast.Attribute) and node.attr == "link"
            and isinstance(node.value, ast.Name) and node.value.id in os_names)


#: a hard link written as a command in a string (`shell=True`, `bash -c`,
#: `os.system`, a Dockerfile's RUN, a compose `command:`): `cp` with a link
#: flag, `ln` without `-s`, rsync's `--link-dest`
#: where a command starts: the text's start, after a shell separator, or
#: after a word that runs one (prose that mentions "ln" or "cp" is no command)
_AT = r"(?:^\s*|[;&|(`]\s*|\b(?:RUN|sudo|exec|then|do|xargs|command:)\s+)"
_CP_LINK = re.compile(_AT + r"cp\s+(?:-[A-Za-z]*\s+)*(?:-[A-Za-z]*l[A-Za-z]*|--link)(?=[\s'\"]|$)")
_LN_HARD = re.compile(_AT + r"ln\s+(?!-[A-Za-z]*s|--symbolic)(?:-[A-Za-z]+\s+)*[^\s-][^\s]*\s+\S")
_LINK_DEST = re.compile(r"--link-dest\b")


def text_hard_links(text: str) -> bool:
    """A hard link in a command written as text."""
    return bool(_CP_LINK.search(text) or _LN_HARD.search(text) or _LINK_DEST.search(text))


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
    tree = ast.parse(source)
    os_names, link_names = {"os"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            os_names |= {a.asname or a.name for a in node.names if a.name == "os"}
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            link_names |= {a.asname or a.name for a in node.names if a.name == "link"}
    os_n, link_n = frozenset(os_names), frozenset(link_names)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if _is_os_link(f, os_n, link_n) or (isinstance(f, ast.Attribute)
                                                and f.attr in ("hardlink_to", "link_to")):
                out.append(node.lineno)
            for kw in node.keywords:
                if kw.arg == "copy_function" and _is_os_link(kw.value, os_n, link_n):
                    out.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module == "os" \
                and any(a.name == "link" for a in node.names):
            out.append(node.lineno)
        elif isinstance(node, (ast.List, ast.Tuple)):
            words = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if len(words) == len(node.elts) and _command_links(words):
                out.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and text_hard_links(node.value):
            out.append(node.lineno)                 # `shell=True`, `bash -c`, `os.system`
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else " X " for v in node.values)
            if text_hard_links(text):
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


def _deploy_texts() -> list[Path]:
    d = ROOT / "deploy"
    return sorted(p for p in d.rglob("*") if p.is_file() and (
        p.name.startswith("Dockerfile") or p.suffix in (".yml", ".yaml")))


def test_no_hard_link_is_made_in_a_dockerfile_or_a_compose_file():
    found = {}
    for p in _deploy_texts():
        lines = [i for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
                 if not line.lstrip().startswith("#") and text_hard_links(line)]
        if lines:
            found[str(p.relative_to(ROOT))] = lines
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
    # under another name, or written as a command string
    assert python_hard_links("import os as o\no.link('a', 'b')\n") == [2]
    assert python_hard_links("from os import link as l\nl('a', 'b')\n") == [1, 2]
    assert python_hard_links("import subprocess\nsubprocess.run('cp -al a b', shell=True)\n") == [2]
    assert python_hard_links("import subprocess\nsubprocess.run(['bash', '-c', 'ln a b'])\n") == [2]
    assert python_hard_links("import os\nos.system(f'cp -l {a} {b}')\n") == [2]
    assert python_hard_links("import subprocess\nsubprocess.run(['rsync', '-a', '--link-dest=x', 'a', 'b'])\n") == [2]
    assert text_hard_links("RUN cp -al /a /b") and text_hard_links("command: rsync --link-dest=/x a b")
    # and what is not one
    for ok in ("ln -sfn a b", "cp -a --reflink=auto a b", "copy the plan", "the ln of x", "kiln is hot",
               "cp -r a b", "ln -s ../x y", "spelling: cp, ln", "a scp -l host", "mcp -l x",
               "run ln a b by hand", "the cp -l flag"):
        assert not text_hard_links(ok), ok
