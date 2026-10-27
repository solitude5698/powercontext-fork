"""Apply model-generated unified diffs without relying on git or patch.

Models routinely emit wrong hunk offsets and mangled line counts, and on this
machine ``core.autocrlf=true`` makes ``git apply`` refuse LF patches outside a
repository, so the diff is matched by content instead of by header numbers.
"""

from __future__ import annotations

import re
from pathlib import Path

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
FILE_HEADER = ("--- ", "+++ ", "index ", "new file mode ", "deleted file mode ",
               "old mode ", "new mode ", "similarity index ", "rename from ",
               "rename to ", "Binary files ")


def _clean_body(body: list[str]) -> list[str]:
    while body and body[-1].strip() == "" and not body[-1].startswith(("+", "-")):
        body.pop()
    return body


def parse(patch_text: str) -> list[tuple[str, list[tuple[int, list[str]]]]]:
    lines = patch_text.split("\n")
    files: list[tuple[str, list[tuple[int, list[str]]]]] = []
    path: str | None = None
    hunks: list[tuple[int, list[str]]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            target = line[4:].strip()
            target = target.split("\t")[0]
            if target == "/dev/null":
                path = None
            else:
                path = target[2:] if target.startswith(("a/", "b/")) else target
            i += 1
            continue
        if line.startswith(FILE_HEADER):
            i += 1
            continue
        match = HUNK_HEADER.match(line)
        if match:
            start = int(match.group(1))
            body: list[str] = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.startswith("@@") or nxt.startswith("diff --git ") or nxt.startswith("--- "):
                    break
                if nxt.startswith("\\"):
                    i += 1
                    continue
                body.append(nxt)
                i += 1
            hunks.append((start, _clean_body(body)))
            continue
        if line.startswith("diff --git "):
            if path and hunks:
                files.append((path, hunks))
            path, hunks = None, []
            i += 1
            continue
        i += 1
    if path and hunks:
        files.append((path, hunks))
    return files


def _sides(body: list[str]) -> tuple[list[str], list[str]]:
    old: list[str] = []
    new: list[str] = []
    for line in body:
        if line.startswith("+"):
            new.append(line[1:])
        elif line.startswith("-"):
            old.append(line[1:])
        else:
            text = line[1:] if line.startswith(" ") else line
            old.append(text)
            new.append(text)
    return old, new


def _locate(lines: list[str], block: list[str], hint: int) -> int | None:
    def same(index: int) -> bool:
        return all(lines[index + k].rstrip() == block[k].rstrip() for k in range(len(block)))

    size = len(block)
    if size == 0:
        return max(0, min(hint, len(lines)))
    if 0 <= hint <= len(lines) - size and same(hint):
        return hint
    for index in range(0, len(lines) - size + 1):
        if same(index):
            return index
    return None


def apply_to_file(root: Path, rel: str, hunks: list[tuple[int, list[str]]]) -> None:
    target = root / rel
    if not target.is_file():
        raise RuntimeError(f"补丁目标文件不存在：{rel}")
    lines = target.read_text(encoding="utf-8").split("\n")
    for start, body in hunks:
        old, new = _sides(body)
        index = _locate(lines, old, start - 1)
        if index is None:
            raise RuntimeError(f"补丁上下文匹配失败：{rel}（@@ -{start}）")
        lines[index:index + len(old)] = new
    text = "\n".join(lines)
    target.write_text(text, encoding="utf-8", newline="\n")


def apply_patch(root: Path, patch_text: str, protected=("tests/",)) -> list[str]:
    files = parse(patch_text)
    if not files:
        raise RuntimeError("补丁中没有可应用的 hunk")
    touched = [rel for rel, _ in files]
    for rel in touched:
        if any(rel.startswith(prefix) for prefix in protected):
            raise RuntimeError(f"补丁试图修改受保护路径：{rel}")
    for rel, hunks in files:
        apply_to_file(root, rel, hunks)
    return touched
