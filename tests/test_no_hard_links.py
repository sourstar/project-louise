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
  below LINE by line, each with its reason, a third party's archive only
  after a refusal of its hard-link members;
- `cp -a`/`--archive`/`-d`/`--preserve=links|all` without
  `--no-preserve=links` (it keeps the links among what it copies),
  `rsync -H`, the `link` command, `cpio -l`, `bsdtar` extracting;
- Python's `extractall`/`extract` with no filter or another filter than
  ours, `shutil.unpack_archive`;
- across a backslash-continued command, and in a multi-line string (a
  script) but not a docstring. tests_campaign/ too.

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
DIRS = ("app", "tools", "deploy", "tests_campaign")

#: shell `tar -x` the deploy runs, each named: they unpack build inputs into
#: a fresh tree (an interpreter, a source stage), never the node's data, so
#: no file the platform holds gets a second name. A new one is reviewed here.
TAR_EXTRACTIONS = {
    ("deploy/vega/switch/rollout-p2.sh", 'tar -xzf "$SRC_STAGE"'): "the staged source tree, into a new stage directory",
    ("deploy/vega/stage/stage_src.sh", 'tar -x -C "$tmp"'): "the source tree, into a temporary stage",
    ("deploy/vega/stage/build_louise_backend.sh", "tar -xzf /cache/python.tgz"):
        "the python-build-standalone runtime, into the package (its hard-link members refused first)",
    ("deploy/vega/stage/build_vega_package.sh", "tar -xzf /cache/python.tgz"):
        "the python-build-standalone runtime, into the package (its hard-link members refused first)",
    ("deploy/16f/deploy-louise-madlad-t5.sh", 'tar -xzf "$TGZ"'): "the release tarball, into a new src directory",
    ("deploy/localai/backends/louise-python/Dockerfile", "tar -xzf /python.tgz"):
        "the python-build-standalone runtime, in an image layer (its hard-link members refused first)",
    ("deploy/vega/switch/backend_files.sh", "--hard-dereference -cf - ."):
        "a copy through a pipe whose creating side dereferences hard links: no link member reaches it",
}
#: archives made by a third party: each extraction follows a refusal of any
#: hard-link member in it (`tar -tv… | grep -q '^h'`, exit 1)
THIRD_PARTY = frozenset(k for k in TAR_EXTRACTIONS if "python.tgz" in k[1])


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


def _values(a: str) -> set[str]:
    return set(a.split("=", 1)[1].split(",")) if "=" in a else set()


def _cp_keeps_links(args: list[str]) -> bool:
    """`cp` keeping the hard links among the files it copies — `-a`/
    `--archive`, `-d`, `--preserve=links|all` — unless `--no-preserve=links`."""
    flags = [a for a in args[1:] if a.startswith("-")]
    keeps = any(a == "--archive" or {"a", "d"} & set(_short(a))
                or (a.startswith("--preserve=") and {"links", "all"} & _values(a)) for a in flags)
    return keeps and not any(a.startswith("--no-preserve=") and {"links", "all"} & _values(a) for a in flags)


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
        return "cp keeping links without --no-preserve=links"
    if head == "ln" and _ln_hard(words):
        return "ln without -s"
    if head in ("tar", "bsdtar") and _tar_extracts(words):
        return "tar extracting"
    if head == "rsync" and any(w == "--hard-links" or "H" in _short(w) for w in words[1:]):
        return "rsync -H"
    if head == "link" and len([w for w in words[1:] if not w.startswith("-")]) == 2:
        return "the link command"
    if head == "cpio" and any(w == "--link" or "l" in _short(w) for w in words[1:]):
        return "cpio -l"
    return None


#: where one command ends and the next begins in a line of shell
_SPLIT = re.compile(r"&&|\|\||[;&|()]|\$\(|`")
#: the prefixes a command may carry in a Dockerfile or a compose file
_PREFIX = re.compile(r"^\s*(?:-\s+|RUN\s+|command:\s*|entrypoint:\s*)")


def _numbered(text: str) -> list[tuple[int, str]]:
    """Each line as (its number, its text), a backslash-continued command
    joined into one and named by its first line."""
    out: list[tuple[int, str]] = []
    cur, start = "", 0
    for i, line in enumerate(text.splitlines(), 1):
        if not cur:
            start = i
        if line.rstrip().endswith("\\") and not line.lstrip().startswith("#"):
            cur += line.rstrip()[:-1] + " "
            continue
        out.append((start, cur + line))
        cur = ""
    if cur:
        out.append((start, cur))
    return out


def _commands(text: str) -> list[list[str]]:
    """Each command of a text, as its words: JSON-style lists (`RUN [...]`,
    compose's `["ln", …]`) and shell lines alike."""
    out: list[list[str]] = []
    for _, line in _numbered(text):
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
    if not isinstance(node.elts[0], ast.Constant) and \
            not any(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts[1:]):
        return None                                      # `(link, src, stage)`: names, no command
    return words


def _receiver(node: ast.Call) -> str:
    r = node.func.value
    return (r.id if isinstance(r, ast.Name) else getattr(r, "attr", "")).lower()


def _tar_call(node: ast.Call) -> bool:
    """An archive's `extractall`/`extract` whose filter is not
    `refuse_hard_links(...)` — none at all included: tarfile's default makes
    an archive's hard-link members as links. A zip's `extractall` (zip holds
    no hard link) and an `extract` of something that is no archive (a PDF's)
    are not one."""
    name = _receiver(node)
    if "zip" in name or name in ("z", "zf"):
        return False
    if node.func.attr == "extract" and not re.search(r"tar|archive|^tf$|^t$", name):
        return False
    flt = next((kw.value for kw in node.keywords if kw.arg == "filter"), None)
    if flt is None:
        return True
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
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and \
                getattr(node.func, "attr", getattr(node.func, "id", None)) == "unpack_archive":
            out.append(node.lineno)                      # shutil's unpacking makes an archive's links
        elif _is_os_link(node, os_n, link_n) and not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)):
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
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings \
                and text_hard_links(node.value):
            out.append(node.lineno)                      # `shell=True`, `os.system`, a script
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else " X " for v in node.values)
            if text_hard_links(text):
                out.append(node.lineno)
    return sorted(set(out))


def shell_hard_links(text: str) -> list[int]:
    """The lines of a shell script that make a hard link (`text_issue`)."""
    return [i for i, line in _numbered(text) if text_issue(line)]


_REFUSAL = re.compile(r"tar -tv\w* .*\| *grep -q '\^h'")


def named_extractions(rel: str, text: str, allow=None, third_party=None) -> tuple[set[int], list[str]]:
    """The `tar -x` lines of `rel` the list names, and what is wrong.

    An entry names EXACTLY ONE line: one that matches two lines names
    neither (a second `tar -x` added after the named one is not named;
    the fourth read of cf91b9ac). A third party's extraction is named only
    with a refusal of hard-link members (`tar -tv… | grep -q '^h'`, then
    `exit 1`) between it and any extraction before it: a probe before EACH
    one."""
    allow = TAR_EXTRACTIONS if allow is None else allow
    third_party = THIRD_PARTY if third_party is None else third_party
    lines = _numbered(text)
    extracting = [(i, line) for i, line in lines if text_issue(line) == "tar extracting"]
    named: set[int] = set()
    wrong: list[str] = []
    for r, part in allow:
        if r != rel:
            continue
        hit = [i for i, line in extracting if part in line]
        if len(hit) != 1:
            wrong.append(f"{part!r} matches {len(hit)} extraction lines, not one")
            continue
        at = hit[0]
        if (r, part) in third_party:
            since = max([i for i, _ in extracting if i < at], default=0)
            probe = [i for i, line in lines if since < i < at and _REFUSAL.search(line)]
            if not probe or not any("exit 1" in line for i, line in lines if probe[-1] <= i < at):
                wrong.append(f"{part!r}: no hard-link refusal between it and the extraction before it")
                continue
        named.add(at)
    return named, wrong


def _named_extraction(rel: str, line: str) -> bool:
    """`line` alone, as the one line of its file (the self-test's form)."""
    return bool(named_extractions(rel, line)[0])


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
        named, wrong = named_extractions(rel, text)
        lines = [i for i, line in _numbered(text) if text_issue(line) and i not in named]
        if lines or wrong:
            found[rel] = lines + wrong
    assert not found, f"hard links (operator, 2026-11-27: never; app/file_copy.py): {found}"


def test_the_named_extractions_are_still_there_a_third_party_s_after_its_links_are_refused():
    """Each named `tar -x` line is still there (the list names only what the
    deploy runs), and a third party's archive is extracted only after a
    refusal of its hard-link members (`tar -tv… | grep -q '^h'`)."""
    for rel in sorted({r for r, _ in TAR_EXTRACTIONS}):
        named, wrong = named_extractions(rel, (ROOT / rel).read_text(encoding="utf-8", errors="replace"))
        assert not wrong, (rel, wrong)
        assert len(named) == sum(1 for r, _ in TAR_EXTRACTIONS if r == rel), rel


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
    bad_py.update({
        "import tarfile\nwith tarfile.open(p) as tar:\n    tar.extractall(d)\n": [3],
        "import tarfile\nwith tarfile.open(p) as tf:\n    tf.extract(m, d)\n": [3],
        "import tarfile\nwith tarfile.open(p) as tar:\n    tar.extractall(d, filter='data')\n": [3],
        "import shutil\nshutil.unpack_archive('a.tar', 'd')\n": [2],
        "import subprocess\nlink = 'ln'\nsubprocess.run([link, 'a', 'b'])\n": [3],
        "s = \"\"\"set -e\nln a b\n\"\"\"\n": [1],
    })
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
    assert shell_hard_links("cp -d a b\ncp --preserve=links a b\ncp -r --preserve=all a b\nrsync -aH a b\n"
                            "link a b\nfind . | cpio -pdl d\nbsdtar -xf a.tar\nrsync --hard-links a b\n") \
        == [1, 2, 3, 4, 5, 6, 7, 8]
    assert shell_hard_links("echo start && \\\n  ln a b\nmkdir x\n") == [1], "a backslash-continued command"
    assert shell_hard_links("rsync -a a b\ncpio -o < l\nbsdtar -tf a.tar\ncp -P a b\n"
                            "cp -a --no-preserve=links a b\ncp --preserve=mode a b\n") == []
    assert python_hard_links('def f():\n    """cp -a a b, say"""\n    return pdf.extract(data)\n') == []
    assert _named_extraction("deploy/vega/stage/stage_src.sh", 'tar -x -C "$tmp"')
    assert not _named_extraction("deploy/vega/stage/stage_src.sh", "tar -xf /other.tar"), "named by its line"
    one = {("x.sh", "tar -xzf /a.tgz")}
    assert named_extractions("x.sh", "tar -xzf /a.tgz -C d\n", one, set()) == ({1}, [])
    twice = named_extractions("x.sh", "tar -xzf /a.tgz -C d\ntar -xzf /a.tgz -C e\n", one, set())
    assert twice[0] == set() and "matches 2" in twice[1][0], "a second line after the named one"
    probe = "if tar -tvzf /a.tgz | grep -q '^h'; then\n  exit 1\nfi\n"
    assert named_extractions("x.sh", probe + "tar -xzf /a.tgz -C d\n", one, one) == ({4}, [])
    assert named_extractions("x.sh", "tar -xzf /b.tgz -C d\ntar -xzf /a.tgz -C d\n", one, one)[0] == set()
    two = {("x.sh", "tar -xzf /a.tgz"), ("x.sh", "tar -xzf /b.tgz")}
    got = named_extractions("x.sh", probe + "tar -xzf /a.tgz -C d\ntar -xzf /b.tgz -C e\n", two, two)
    assert got[0] == {4} and "no hard-link refusal" in got[1][0], "a probe before EACH extraction"
    assert python_hard_links("def f(link, src, stage):\n    return link, src, stage\n") == []
    assert python_hard_links("import zipfile\nwith zipfile.ZipFile(p) as zf:\n    zf.extractall(d)\n") == []
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
    assert python_hard_links("import tarfile\nt.extractall(d, filter='data')\n") == [2]
    assert python_hard_links("t.extractall(d, filter=file_copy.refuse_hard_links('data'))\n") == []


def _archive_backend():
    import importlib.util
    spec = importlib.util.spec_from_file_location("archive_backend", ROOT / "tools" / "archive_backend.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_archive_backend_writes_files_sharing_an_inode_whole_and_unpacks_old_links_as_copies(tmp_path):
    """Finding 6 of the third read: its writer made hard-link entries that its
    reader refused. Now each such file is written whole; an older archive's
    hard-link entry is unpacked as a COPY of its target, never a link."""
    import io
    import os
    import tarfile
    mod = _archive_backend()
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a").write_bytes(b"same bytes")
    os.link(tree / "a", tree / "b")                         # the input's own link, as a venv may hold
    if not hasattr(mod, "_zstd_writer"):
        pytest.skip("no zstd writer")
    try:
        mod.write_archive(tree, tmp_path / "t.tar.zst")
    except FileNotFoundError:
        pytest.skip("zstd is not installed here")
    with mod._zstd_reader(tmp_path / "t.tar.zst") as r, tarfile.open(fileobj=r, mode="r|") as tar:
        kinds = {m.name: m.type for m in tar}
    assert kinds["a"] == kinds["b"] == tarfile.REGTYPE, "written whole, no hard-link entry"
    mod.unpack(tmp_path / "t.tar.zst", tmp_path / "out")
    assert (tmp_path / "out" / "b").read_bytes() == b"same bytes" and (tmp_path / "out" / "b").stat().st_nlink == 1
    # an archive written before: its hard-link entry becomes a copy
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        info = tarfile.TarInfo("f")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"abc"))
        link = tarfile.TarInfo("g")
        link.type, link.linkname = tarfile.LNKTYPE, "f"
        tar.addfile(link)
    with mod._zstd_writer(tmp_path / "old.tar.zst") as w:
        w.write(raw.getvalue())
    mod.unpack(tmp_path / "old.tar.zst", tmp_path / "old")
    g = tmp_path / "old" / "g"
    assert g.read_bytes() == b"abc" and g.stat().st_nlink == 1 and (tmp_path / "old" / "f").stat().st_nlink == 1


def test_a_copy_holds_the_bytes_of_a_symbolic_link_never_the_link(tmp_path):
    """Finding 8 of the third read: the seed's copy of a part a symbolic
    link names holds the part's bytes."""
    import os
    from app import file_copy
    real = tmp_path / "real.part"
    real.write_bytes(b"the part")
    link = tmp_path / "link.part"
    link.symlink_to(real)
    file_copy.copy_file(link, tmp_path / "copy.part")
    got = tmp_path / "copy.part"
    assert not got.is_symlink() and got.read_bytes() == b"the part" and os.stat(got).st_nlink == 1
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "p").symlink_to(real)
    file_copy.copy_tree(tree, tmp_path / "tree2")
    assert not (tmp_path / "tree2" / "p").is_symlink() and (tmp_path / "tree2" / "p").read_bytes() == b"the part"
