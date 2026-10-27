"""Index specs, source code, and tests into seekdb."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterator

HEADING = re.compile(r"^(#{2,3})\s+(.*)$", re.MULTILINE)


def _spec_units(path: Path, root: Path) -> Iterator[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    matches = list(HEADING.finditer(text))
    rel = path.relative_to(root).as_posix()
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        title = match.group(2).strip()
        yield {
            "id": f"spec:{rel}#{title}",
            "kind": "spec",
            "path": rel,
            "symbol": title,
            "title": title,
            "content": f"{rel} · {title}\n{text[start:end].strip()}",
        }


def _symbol_units(path: Path, root: Path) -> Iterator[dict[str, str]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    rel = path.relative_to(root).as_posix()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            segment = ast.get_source_segment(source, node)
            if not segment:
                continue
            yield {
                "id": f"code:{rel}#{node.name}",
                "kind": "code",
                "path": rel,
                "symbol": node.name,
                "title": node.name,
                "content": f"{rel} · {node.name}\n{segment}",
            }
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if not names:
            continue
        segment = ast.get_source_segment(source, node) or ""
        symbol = names[0]
        yield {
            "id": f"code:{rel}#{symbol}",
            "kind": "code",
            "path": rel,
            "symbol": symbol,
            "title": symbol,
            "content": f"{rel} · {symbol}\n{segment}",
        }


def _test_units(path: Path, root: Path) -> Iterator[dict[str, str]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    rel = path.relative_to(root).as_posix()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            segment = ast.get_source_segment(source, node) or ""
            yield {
                "id": f"test:{rel}#{node.name}",
                "kind": "test",
                "path": rel,
                "symbol": node.name,
                "title": node.name,
                "content": f"{rel} · {node.name}\n{segment}",
            }


def collect_units(project_dir: Path) -> list[dict[str, str]]:
    units: list[dict[str, str]] = []
    for path in sorted((project_dir / "specs").rglob("*.md")):
        units.extend(_spec_units(path, project_dir))
    for path in sorted((project_dir / "src").rglob("*.py")):
        units.extend(_symbol_units(path, project_dir))
    for path in sorted((project_dir / "tests").rglob("test_*.py")):
        units.extend(_test_units(path, project_dir))
    return units
