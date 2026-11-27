"""NO HARD LINKS (operator, 2026-11-27, verbatim: "Never use hard links,
always consciously load from a path. It already happened that you deleted
important files because you forgot the hard links"; app/file_copy.py).

Nothing in app/, tools/ or deploy/ — nor the root Dockerfile and compose
file — makes a second name for a file:
- `os.link` called or referenced (under any name it was imported as),
  `Path.hardlink_to`/`link_to`;
- a command, as a list or as text (`shell=True`, `bash -c`, `os.system`,
  a script, a Dockerfile's RUN, a compose `command:` or `- ` item): `cp`
  with a link flag (`-l`, `-al`, `--link`), `ln` without `-s`/`--symbolic`,
  rsync's `--link-dest`, or `tar` extracting files (an archive's hard-link
  members are made as links) — the extractions the deploy needs are named
  below, each with its reason;
- `cp -a`/`--archive` without `--no-preserve=links` (it keeps the links
  among what it copies).

A symbolic link names its target's path and is allowed; a sister's seeded
SHARD parts are neither: her own copy (operator, 2026-11-27, choice B;
tests/hold/test_carry.py). Python's own extraction refuses link members in
words (`app.file_copy.refuse_hard_links`)."""
from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIRS = ("app", "tools", "deploy")

#: shell `tar -x` the deploy runs, each named: they unpack build inputs into
#: a fresh tree (an interpreter, a source stage), never the node's data, so
#: no file the platform holds gets a second name. A new one is reviewed here.
TAR_EXTRACTIONS = {
    "deploy/vega/switch/rollout-p2.sh": "the staged source tree, into a new stage directory",
    "deploy/vega/stage/stage_src.sh": "the source tree, into a temporary stage",
    "deploy/vega/stage/build_louise_backend.sh": "the python-build-standalone runtime, into the package",
    "deploy/vega/stage/build_vega_package.sh": "the python-build-standalone runtime, into the package",
    "deploy/16f/deploy-louise-madlad-t5.sh": "the release tarball, into a new src directory",
    "deploy/localai/backends/louise-python/Dockerfile": "the python-build-standalone runtime, in an image layer",
    "deploy/vega/switch/backend_files.sh": "a copy through a pipe whose creating side dereferences hard "
                                           "links (--hard-dereference): no link member reaches it",
}


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


def _short(a: str) -> str:
    return a[1:] if a.startswith("-") and not a.startswith("--") else ""


def _cp_links(args: list[str]) -> bool:
    flags = [a for a in args[1:] if a.startswith("-")]
    return any(a == "--link" or "l" in _short(a) for a in flags)


def _cp_keeps_links(args: list[str]) -> bool:
    """`cp -a`/`--archive` keeps the hard links among the files it copies,
    unless `--no-preserve=links` is given."""
    flags = [a for a in args[1:] if a.startswith("-")]
    archive = any(a == "--archive" or "a" in _short(a) for a in flags)
    return archive and not any(a.startswith("--no-preserve=") and "links" in a.split("=", 1)[1].split(",")
                               for a in flags)


def _ln_hard(args: list[str]) -> bool:
    """`ln` with an operand (the word alone is no command) and no `-s`."""
    flags = [a for a in args[1:] if a.startswith("-")]
    operands = [a for a in args[1:] if not a.startswith("-")]
    return bool(operands) and not any(a == "--symbolic" or "s" in _short(a) for a in flags)


def _tar_extracts(args: list[str]) -> bool:
    """`tar` extracting files: `-x…`, a first bare word with `x`, `--extract`
    or `--get`; to standard output (`-O`, `--to-stdout`) is no file."""
    rest = args[1:]
    if not rest:
        return False
    flags = [a for a in rest if a.startswith("-")]
    extracting = (any(a in ("--extract", "--get") or "x" in _short(a) for a in flags)
                  or (not rest[0].startswith("-") and "x" in rest[0] and rest[0].isalpha()))
    to_stdout = any(a == "--to-stdout" or "O" in _short(a) for a in flags) or \
        (not rest[0].startswith("-") and "O" in rest[0])
    return extracting and not to_stdout


def command_issue(words: list[str]) -> str | None:
    """What makes a hard link in one command's words, or None."""
    words = list(words)
    while words and ("=" in words[0] and not words[0].startswith("-")
                     or words[0] in ("sudo", "exec", "command", "then", "do", "else", "!", "(", "{",
                                     "xargs", "env", "nice", "time")):
        words = words[1:]
    if not words:
        return None
    if any(w.startswith("--link-dest") for w in words):
        return "--link-dest"
    head = Path(words[0]).name
    if head == "cp" and _cp_links(words):
        return "cp with a link flag"
    if head == "cp" and _cp_keeps_links(words):
        return "cp -a without --no-preserve=links"
    if head == "ln" and _ln_hard(words):
        return "ln without -s"
    if head == "tar" and _tar_extracts(words):
        return "tar extracting"
    return None


#: where one command ends and the next begins in a line of shell
_SPLIT = re.compile(r"&&|\|\||[;&|()]|\$\(|`")
#: the prefixes a command may carry in a Dockerfile or a compose file
_PREFIX = re.compile(r"^\s*(?:-\s+|RUN\s+|command:\s*|entrypoint:\s*)")


def _commands(text: str) -> list[list[str]]:
    """Each command of a text, as its words: JSON-style lists (`RUN [...]`,
    compose's `["ln", …]`) and shell lines alike."""
    out: list[list[str]] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        body = _PREFIX.sub("", line, count=1)
        if body.lstrip().startswith("["):
            words = re.findall(r'"((?:[^"\\]|\\.)*)"', body) or re.findall(r"'([^']*)'", body)
            if words:
                out.append(words)
                for w in words[1:]:
                    if " " in w:                         # `["bash", "-c", "ln a b"]`
                        out += _commands(w)
                continue
        code = body.split(" #", 1)[0]
        for part in _SPLIT.split(code):
            try:
                words = shlex.split(part)
            except ValueError:
                words = part.split()
            if words:
                out.append(words)
    return out


def text_issue(text: str) -> str | None:
    for words in _commands(text):
        got = command_issue(words)
        if got:
            return got
    return None


def text_hard_links(text: str) -> bool:
    return text_issue(text) is not None


def _const_words(node: ast.AST) -> list[str] | None:
    """A list/tuple's words where its HEAD may be a variable (`[cp, "-al",
    …]`): the variable's name stands for the command it holds."""
    if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
        return None
    words: list[str] = []
    for i, e in enumerate(node.elts):
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            words.append(e.value)
        elif i == 0 and isinstance(e, ast.Name):
            words.append(e.id.lower().removesuffix("_bin").removesuffix("_path"))
        elif i == 0 and isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) \
                and e.func.attr == "which" and e.args and isinstance(e.args[0], ast.Constant):
            words.append(str(e.args[0].value))           # `shutil.which("cp")`
        else:
            words.append("X")
    return words


def _tar_call(node: ast.Call) -> bool:
    """An `extractall`/`extract` whose filter is not `refuse_hard_links(...)`."""
    flt = next((kw.value for kw in node.keywords if kw.arg == "filter"), None)
    if flt is None:
        return False                                     # no tarfile filter named (a zip, a PDF's `extract`)
    return not (isinstance(flt, ast.Call) and getattr(flt.func, "attr", getattr(flt.func, "id", None))
                == "refuse_hard_links")


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
        if _is_os_link(node, os_n, link_n) and not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)):
            out.append(node.lineno)                      # called or handed on (`copy_function`)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("hardlink_to", "link_to"):
            out.append(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("extractall", "extract") and _tar_call(node):
            # tarfile's filters ("data", "tar") make an archive's hard-link
            # members as links: only `refuse_hard_links(...)` extracts
            out.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module == "os" \
                and any(a.name == "link" for a in node.names):
            out.append(node.lineno)
        elif (words := _const_words(node)) is not None:
            if command_issue(words):
                out.append(node.lineno)
            for w in words[1:]:
                if " " in w and text_hard_links(w):      # `["bash", "-c", "ln a b"]`
                    out.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "\n" not in node.value \
                and text_hard_links(node.value):
            out.append(node.lineno)                      # `shell=True`, `os.system`
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else " X " for v in node.values)
            if text_hard_links(text):
                out.append(node.lineno)
    return sorted(set(out))


def shell_hard_links(text: str) -> list[int]:
    """The lines of a shell script that make a hard link (`text_issue`)."""
    return [i for i, line in enumerate(text.splitlines(), 1) if text_issue(line)]


def _named_extraction(rel: str, line: str) -> bool:
    return rel in TAR_EXTRACTIONS and text_issue(line) == "tar extracting"


def test_no_hard_link_is_made_in_python():
    found = {str(p.relative_to(ROOT)): lines for p in _files(".py")
             if (lines := python_hard_links(p.read_text(encoding="utf-8")))}
    assert not found, f"hard links (operator, 2026-11-27: never; app/file_copy.py): {found}"


def _deploy_texts() -> list[Path]:
    out = [p for p in (ROOT / "deploy").rglob("*") if p.is_file() and (
        p.name.startswith("Dockerfile") or p.name.endswith(".Dockerfile") or p.suffix in (".yml", ".yaml"))]
    out += [p for p in ROOT.iterdir() if p.is_file() and (
        p.name.startswith("Dockerfile") or p.name.startswith("docker-compose"))]
    return sorted(set(out))


def test_no_hard_link_is_made_in_shell_dockerfiles_or_compose_files():
    found = {}
    for p in _files(".sh") + _deploy_texts():
        rel = str(p.relative_to(ROOT))
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = [i for i, line in enumerate(text.splitlines(), 1)
                 if text_issue(line) and not _named_extraction(rel, line)]
        if lines:
            found[rel] = lines
    assert not found, f"hard links (operator, 2026-11-27: never; app/file_copy.py): {found}"


def test_the_named_extractions_are_still_there():
    """A named `tar -x` that is gone is taken off the list (it names only
    what the deploy runs)."""
    for rel in TAR_EXTRACTIONS:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        assert any(text_issue(line) == "tar extracting" for line in text.splitlines()), rel


def test_the_guard_finds_what_it_is_for():
    bad_py = {
        "import os\nos.link('a', 'b')\n": [2],
        "from pathlib import Path\nPath('b').hardlink_to('a')\n": [2],
        "from os import link\n": [1],
        "import os, shutil\nshutil.copytree('a', 'b', copy_function=os.link)\n": [2],
        "import os, shutil\nshutil.copytree('a', 'b', False, None, os.link)\n": [2],
        "import os\nf = os.link\n": [2],
        "import subprocess\nsubprocess.run(['cp', '-al', 'a', 'b'])\n": [2],
        "import subprocess\nsubprocess.run(['ln', 'a', 'b'])\n": [2],
        "import os as o\no.link('a', 'b')\n": [2],
        "from os import link as l\nl('a', 'b')\n": [1, 2],
        "import subprocess\nsubprocess.run('cp -al a b', shell=True)\n": [2],
        "import subprocess\nsubprocess.run(['bash', '-c', 'ln a b'])\n": [2],
        "import os\nos.system(f'cp -l {a} {b}')\n": [2],
        "import subprocess\nsubprocess.run(['rsync', '-a', '--link-dest=x', 'a', 'b'])\n": [2],
        "import subprocess\ncp = 'cp'\nsubprocess.run([cp, '-al', 'a', 'b'])\n": [3],
        "import subprocess, shutil\nsubprocess.run([shutil.which('cp'), '-a', 'a', 'b'])\n": [2],
        "import subprocess\nsubprocess.run(['ln', '--force', 'a', 'b'])\n": [2],
        "import subprocess\nsubprocess.run(['cp', '--archive', '--link', 'a', 'b'])\n": [2],
    }
    for src, want in bad_py.items():
        assert python_hard_links(src) == want, src
    for ok in ("from pathlib import Path\nPath('b').symlink_to('a')\n",
               "import subprocess\nsubprocess.run(['cp', '-a', '--no-preserve=links', '--reflink=auto', 'a', 'b'])\n",
               "import subprocess\nsubprocess.run(['ln', '-sfn', 'a', 'b'])\n",
               "import subprocess\nsubprocess.run(['ln', '-f', '-s', 'a', 'b'])\n",
               "lines = [ln for ln in x if ln]\n", "link = 'a'\n"):
        assert python_hard_links(ok) == [], ok
    assert shell_hard_links("ln a b\n") == [1]
    assert shell_hard_links("cp -al a b\ncp --link a b\ncp --archive --link a b\n") == [1, 2, 3]
    assert shell_hard_links("x=1 && ln a b\nln --force a b\ntar -xzf a.tgz -C b\ntar xf a.tar\n") == [1, 2, 3, 4]
    assert shell_hard_links("ln -s a b\nln -sfn \"$a\" b\nln -f -s a b\ncp -a --no-preserve=links a b\n"
                            "# ln a b\ntar -xOf a.tar m.json\ntar -czf a.tgz b\n") == []
    assert shell_hard_links("cp -a a b\n") == [1], "keeps the links among what it copies"
    for bad in ("RUN cp -al /a /b", "command: rsync --link-dest=/x a b", 'RUN ["ln", "a", "b"]',
                '    command: ["ln", "a", "b"]', "  - ln a b", 'RUN ["bash", "-c", "ln a b"]'):
        assert text_hard_links(bad), bad
    for ok in ("ln -sfn a b", "cp -a --no-preserve=links --reflink=auto a b", "copy the plan",
               "the ln of x", "kiln is hot", "cp -r a b", "ln -s ../x y", "spelling: cp, ln",
               "a scp -l host", "mcp -l x", "run ln a b by hand", "the cp -l flag", "ln -f -s a b",
               "  - lnx a", 'RUN ["ln", "-s", "a", "b"]'):
        assert not text_hard_links(ok), ok


def test_an_archive_s_hard_link_member_is_refused_in_words(tmp_path):
    """Finding 8's two extractions: tarfile's "data" filter makes a hard-link
    member whose target is inside the destination; ours refuses it."""
    import importlib.util
    import io
    import tarfile
    from app import file_copy
    arc = tmp_path / "a.tar"
    with tarfile.open(arc, "w") as tar:
        data = b"x"
        info = tarfile.TarInfo("f")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("g")
        link.type, link.linkname = tarfile.LNKTYPE, "f"
        tar.addfile(link)
    with tarfile.open(arc) as tar, pytest.raises(file_copy.HardLinkMember, match="g is a hard link to f"):
        tar.extractall(tmp_path / "out", filter=file_copy.refuse_hard_links("data"))
    spec = importlib.util.spec_from_file_location("archive_backend", ROOT / "tools" / "archive_backend.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with tarfile.open(arc) as tar, pytest.raises(mod.HardLinkMember):
        tar.extractall(tmp_path / "out2", filter=mod.refuse_hard_links("tar"))
    assert python_hard_links("import tarfile\nt.extractall(d, filter='data')\n") == [2]
    assert python_hard_links("t.extractall(d, filter=file_copy.refuse_hard_links('data'))\n") == []
