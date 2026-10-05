"""The multi-file format authoring models write tasks in, and its safe materialisation.

A bundle is plain text the model can produce reliably:

    === FILE: environment/repo/app.py ===
    <content>
    === FILE: instruction.md ===
    <content>
    === END ===

`materialise` writes it under a task directory, refusing absolute paths, `..`, symlinks, files
outside the allowed top-level folders, and oversized bundles. `render` turns a task directory back
into a bundle (to show a model an exemplar or ask it to revise its own draft).
"""

import re
from pathlib import Path

HEADER = re.compile(r"^=== FILE: (.+?) ===\s*$", re.M)
END = "=== END ==="
ALLOWED_TOP = {"task.toml", "instruction.md", "build_world.py", "environment", "solution", "tests", "wrong_solutions"}
MAX_FILES = 200
MAX_BYTES = 2_000_000
EXECUTABLE = re.compile(r"\.sh$")


class BundleError(ValueError):
    pass


def parse(text: str) -> dict[str, str]:
    body = text.split(END, 1)[0]
    marks = list(HEADER.finditer(body))
    if not marks:
        raise BundleError("no '=== FILE: <path> ===' headers found")
    files = {}
    for i, m in enumerate(marks):
        path = m.group(1).strip()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        content = body[m.end():end]
        files[path] = content[1:] if content.startswith("\n") else content
    return files


def _check_path(path: str) -> None:
    p = Path(path)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise BundleError(f"unsafe path: {path!r}")
    if p.parts[0] not in ALLOWED_TOP:
        raise BundleError(f"path outside the task layout: {path!r}")


def materialise(files: dict[str, str], task_dir: Path, *, replace: bool = True) -> list[Path]:
    if len(files) > MAX_FILES:
        raise BundleError(f"too many files ({len(files)} > {MAX_FILES})")
    if sum(len(c.encode()) for c in files.values()) > MAX_BYTES:
        raise BundleError("bundle too large")
    for path in files:
        _check_path(path)
    task_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for path, content in sorted(files.items()):
        dest = task_dir / path
        for parent in [task_dir / Path(*Path(path).parts[:i]) for i in range(1, len(Path(path).parts))]:
            if parent.is_symlink():
                raise BundleError(f"refusing to write through a symlink: {parent}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            raise BundleError(f"refusing to overwrite a symlink: {dest}")
        if dest.exists() and not replace:
            raise BundleError(f"{path} exists")
        dest.write_text(content if content.endswith("\n") else content + "\n")
        if EXECUTABLE.search(path):
            dest.chmod(0o755)
        written.append(dest)
    return written


def render(task_dir: Path, include: tuple[str, ...] = ("task.toml", "instruction.md", "environment", "solution",
                                                       "tests", "wrong_solutions"),
           skip_dirs: tuple[str, ...] = ("skills", "__pycache__")) -> str:
    out = []
    for top in include:
        p = task_dir / top
        paths = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file()) if p.exists() else []
        for f in paths:
            rel = f.relative_to(task_dir)
            if any(part in skip_dirs for part in rel.parts):
                continue
            try:
                text = f.read_text()
            except UnicodeDecodeError:
                continue
            out.append(f"=== FILE: {rel} ===\n" + (text if text.endswith("\n") else text + "\n"))
    return "".join(out) + f"{END}\n"
