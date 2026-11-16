"""The code a measurement run executes, as one digest: the run's own module
and every module of this repository it imports, at any depth, the imports
inside functions included (§13 of legal_pilot: a whole run runs its pilot's
code). A pilot-ok is bound to it through the run's tag.

WHY NOT A HAND LIST, NOR ASK'S CODE (2026-11-16). The retrieval and chunker
tags pinned `chat_eval.ask_code()` — app/ask's files, which a retrieval run
does not execute — beside six files listed by hand. A deploy that touched only
Ask (09162b0) re-ran the chunker pilot for nothing, and a module the run does
import but the list did not name (legal_pilot's own scoring) could change
under a pilot-ok unnoticed.

THE STANDARD, NOT ADOPTED: the standard library's `modulefinder` walks the
same graph from bytecode, but raises on a namespace package (tools/measure has
no `__init__.py`: its `_find_module` reads `spec.loader.is_package` and the
loader is None). The same rule is walked here over `ast`: every `import` and
`from … import` statement anywhere in a module, resolved to this repository's
files; third-party and standard modules are not followed (the image pins their
versions). A module that is imported but not executed by a given run is still
counted: the pin is conservative, never blind.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: the repository's top-level packages a run's imports are followed into
PACKAGES = ("app", "tools")


def _resolve(name: str, root: Path) -> list[Path]:
    """The repository files a dotted name may be: each package's
    `__init__.py` on the way, and the module itself. A name below a module
    (`app.db.connect`) stops at the module."""
    parts = name.split(".")
    if not parts or parts[0] not in PACKAGES:
        return []
    out: list[Path] = []
    for i in range(1, len(parts) + 1):
        base = root.joinpath(*parts[:i])
        module = base.with_suffix(".py")
        if module.is_file():
            out.append(module)
            break
        if not base.is_dir():
            break
        init = base / "__init__.py"
        if init.is_file():
            out.append(init)
    return out


def _imports(path: Path, root: Path) -> set[str]:
    """Every dotted name the module at `path` imports, anywhere in it;
    `from a import b` gives `a` and `a.b` (b may be a submodule)."""
    tree = ast.parse(path.read_bytes(), filename=str(path))
    package = list(path.relative_to(root).with_suffix("").parts[:-1])
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level - 1 > len(package):
                    continue
                base = package[: len(package) - (node.level - 1)]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            if not module:
                continue
            names.add(module)
            names.update(f"{module}.{a.name}" for a in node.names if a.name != "*")
    return names


def closure(entry: Path, root: Path = ROOT) -> list[Path]:
    """The repository files `entry` executes or imports, sorted."""
    root = root.resolve()
    seen: set[Path] = set()
    todo = [Path(entry).resolve()]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        for name in _imports(path, root):
            todo.extend(f.resolve() for f in _resolve(name, root) if f.resolve() not in seen)
    return sorted(seen)


def digest(entry: Path, root: Path = ROOT) -> str:
    """`sha256:` over each file of `closure(entry)`: its path under `root`
    and its bytes."""
    root = root.resolve()
    h = hashlib.sha256()
    for path in closure(entry, root):
        h.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return "sha256:" + h.hexdigest()
