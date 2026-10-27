"""Command line entry point of the SDD context module."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from .config import Config, ROOT
from .db import Store
from .indexer import collect_units
from .mini import MiniMax, MiniMaxError
from . import patch as sdd_patch
from .pc import PowerContext, PowerContextError
from .selector import build_context, history_query, render_prompt

DIFF_BLOCK = re.compile(r"```(?:diff)?[ \t]*\r?\n(.*?)```", re.S)
THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S)


def read_task(path: Path) -> tuple[str, str]:
    return path.parent.name, path.read_text(encoding="utf-8")


def prepare_workspace(cfg: Config, task_id: str) -> Path:
    workdir = cfg.work_dir / task_id
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cfg.project_dir, workdir)
    return workdir


def run_pytest(workdir: Path, target: str | None = None) -> tuple[int, str]:
    args = [sys.executable, "-m", "pytest", "-q"]
    if target:
        args.append(target)
    proc = subprocess.run(args, cwd=workdir, capture_output=True, text=True, encoding="utf-8")
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def extract_patch(answer: str) -> str:
    text = THINK_BLOCK.sub("", answer)
    diffs = [
        block for block in DIFF_BLOCK.findall(text)
        if block.lstrip().startswith(("diff --git", "--- "))
    ]
    if diffs:
        candidate = max(diffs, key=len)
    else:
        candidate = text
    start = candidate.find("diff --git")
    if start == -1:
        start = candidate.find("--- ")
    if start == -1:
        raise RuntimeError("MiniMax 回答中没有找到 unified diff")
    patch = candidate[start:]
    if not patch.endswith("\n"):
        patch += "\n"
    return repair_hunk_headers(patch.replace("\r\n", "\n"))


HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$")


def repair_hunk_headers(patch: str) -> str:
    """Recount hunk lines: models often emit wrong counts in @@ headers."""
    lines = patch.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        match = HUNK_HEADER.match(lines[i])
        if not match:
            out.append(lines[i])
            i += 1
            continue
        old_start, new_start, tail = match.groups()
        body: list[str] = []
        i += 1
        while i < len(lines):
            line = lines[i]
            if line.startswith(("@@ ", "diff --git")):
                break
            body.append(line)
            i += 1
        while body and body[-1].strip() == "" and not body[-1].startswith(("+", "-")):
            body.pop()
        old_count = sum(1 for line in body if not line.startswith("+"))
        new_count = sum(1 for line in body if not line.startswith("-"))
        out.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{tail}")
        out.extend(body)
    return "\n".join(out).rstrip("\n") + "\n"


def apply_patch(workdir: Path, patch_path: Path) -> list[str]:
    patch_text = patch_path.read_text(encoding="utf-8")
    return sdd_patch.apply_patch(workdir, patch_text)


def cmd_index(cfg: Config) -> int:
    store = Store(cfg)
    store.ensure_schema()
    store.reset()
    units = collect_units(cfg.project_dir)
    count = store.upsert(units)
    kinds = {}
    for unit in units:
        kinds[unit["kind"]] = kinds.get(unit["kind"], 0) + 1
    print(f"已写入 seekdb：{count} 条（{kinds}）")
    return 0


def cmd_context(cfg: Config, task_file: Path) -> int:
    store = Store(cfg)
    store.ensure_schema()
    task_id, task_text = read_task(task_file)
    pc = PowerContext(cfg)
    context = build_context(task_text, cfg.project_dir, store, pc, task_id)
    evidence = cfg.evidence_for(task_id)
    (evidence / "context.json").write_text(
        json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    store.flush_query_log(evidence / "seekdb-queries.log")
    pc.flush(evidence / "powercontext-calls.jsonl")
    print(_summary(context))
    return 0


def _summary(context: dict) -> str:
    lines = [f"任务：{context['task_id']}"]
    for key, label in (("specs", "规格"), ("code", "代码"), ("tests", "测试")):
        lines.append(f"{label}：")
        for item in context[key]:
            name = item.get("symbol") or item.get("title")
            lines.append(f"  - {item['id']}  score={item['score']}")
    history = context["history"].strip() or "无历史状态"
    lines.append("PowerContext 历史状态：")
    lines.append("  " + history.replace("\n", "\n  ")[:600])
    return "\n".join(lines)


def cmd_fix(cfg: Config, task_file: Path) -> int:
    store = Store(cfg)
    store.ensure_schema()
    task_id, task_text = read_task(task_file)
    evidence = cfg.evidence_for(task_id)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    pc = PowerContext(cfg)

    workdir = prepare_workspace(cfg, task_id)
    context = build_context(task_text, cfg.project_dir, store, pc, task_id)
    (evidence / "context.json").write_text(
        json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    before_code, before_log = run_pytest(workdir, "tests/test_gold_plus.py")
    full_before_code, full_before_log = run_pytest(workdir)
    (evidence / "before-pytest.txt").write_text(
        f"$ python -m pytest -q tests/test_gold_plus.py\nexit={before_code}\n{before_log}\n\n"
        f"$ python -m pytest -q\nexit={full_before_code}\n{full_before_log}\n",
        encoding="utf-8",
    )

    client = MiniMax(cfg, evidence / "minimax-calls.jsonl")
    answer = client.chat(render_prompt(context))
    (evidence / "minimax-answer.md").write_text(answer, encoding="utf-8")

    patch = extract_patch(answer)
    patch_path = evidence / "change.patch"
    with patch_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(patch)
    touched = apply_patch(workdir, patch_path)

    after_code, after_log = run_pytest(workdir)
    (evidence / "after-pytest.txt").write_text(
        f"$ python -m pytest -q\nexit={after_code}\n{after_log}\n", encoding="utf-8"
    )

    status = "passed" if after_code == 0 and before_code != 0 else "failed"
    summary = (
        f"任务 {task_id} 运行 {run_id}：修改前目标测试 exit={before_code}，"
        f"修改后全量测试 exit={after_code}。"
        f"选中的规格 {[i['id'] for i in context['specs']]}，"
        f"修改文件 {touched}。"
    )
    decisions = [
        f"{task_id}: 采用 MiniMax {cfg.minimax_model} 生成补丁，修改后全量测试 exit={after_code}",
    ]
    state = {
        "run_id": run_id,
        "task_id": task_id,
        "status": status,
        "before_exit": before_code,
        "after_exit": after_code,
        "selected": {
            "specs": [i["id"] for i in context["specs"]],
            "code": [i["id"] for i in context["code"]],
            "tests": [i["id"] for i in context["tests"]],
        },
        "summary": summary,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    try:
        pc.remember_run(task_id, run_id, summary, decisions)
        state["powercontext"] = "saved"
    except PowerContextError as exc:
        state["powercontext"] = f"failed: {exc}"
        print(f"[warn] PowerContext 写入失败：{exc}", file=sys.stderr)
    pc.flush(evidence / "powercontext-calls.jsonl")
    store.record_run(run_id, task_id, status, state)
    store.flush_query_log(evidence / "seekdb-queries.log")
    (evidence / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"修改前：目标测试 exit={before_code}，全量 exit={full_before_code}")
    print(f"修改后：全量 exit={after_code}")
    print(f"状态：{status}；证据目录：{evidence}")
    return 0 if status == "passed" else 1


def cmd_status(cfg: Config, task_file: Path) -> int:
    task_id, task_text = read_task(task_file)
    pc = PowerContext(cfg)
    try:
        content = pc.recall(history_query(task_id, task_text))
    except PowerContextError as exc:
        print(f"PowerContext 不可用：{exc}")
        return 1
    print(f"任务 {task_id} 的历史状态（来自 PowerContext）：")
    print(content.strip() or "无历史状态")
    evidence = cfg.evidence_for(task_id)
    pc.flush(evidence / "powercontext-calls.jsonl")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sdd", description="SDD 工程上下文模块")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("index", help="把规格、代码、测试写入 seekdb")

    context_cmd = sub.add_parser("context", help="为任务选择上下文")
    context_cmd.add_argument("--task", required=True, type=Path)

    fix_cmd = sub.add_parser("fix", help="生成补丁、应用并运行测试")
    fix_cmd.add_argument("--task", required=True, type=Path)

    status_cmd = sub.add_parser("status", help="读取 PowerContext 中的任务状态")
    status_cmd.add_argument("--task", required=True, type=Path)

    args = parser.parse_args(argv)
    env_file = Path(args.env_file)
    cfg = Config.load(env_file if env_file.is_file() else None)

    if args.command == "index":
        return cmd_index(cfg)
    if args.command == "context":
        return cmd_context(cfg, args.task)
    if args.command == "fix":
        return cmd_fix(cfg, args.task)
    return cmd_status(cfg, args.task)


if __name__ == "__main__":
    raise SystemExit(main())
