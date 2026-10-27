"""Select the engineering context a task needs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import Store
from .pc import PowerContext


def history_query(task_id: str, task_text: str) -> str:
    """Keep the recall query on one line: multi-line text makes prepare return empty."""
    for line in task_text.splitlines():
        title = line.lstrip("# ").strip()
        if title:
            return f"{task_id} {title}"
    return task_id


def build_context(
    task_text: str,
    project_dir: Path,
    store: Store,
    pc: PowerContext,
    task_id: str,
) -> dict[str, Any]:
    specs = store.search(task_text, ("spec",), limit=6)
    code = store.search(task_text, ("code",), limit=4)
    tests = store.search(task_text, ("test",), limit=10)

    history = ""
    try:
        history = pc.recall(history_query(task_id, task_text))
    except Exception as exc:  # PowerContext 不可用时仍能给出上下文
        history = f"（PowerContext 读取失败：{exc}）"

    code_files: dict[str, str] = {}
    for unit in code:
        path = unit["path"]
        if path not in code_files:
            code_files[path] = (project_dir / path).read_text(encoding="utf-8")

    return {
        "task_id": task_id,
        "task": task_text.strip(),
        "specs": [{"id": u["id"], "path": u["path"], "title": u["title"],
                   "score": u["score"], "content": u["content"]} for u in specs],
        "code": [{"id": u["id"], "path": u["path"], "symbol": u["symbol"],
                  "score": u["score"], "content": u["content"]} for u in code],
        "tests": [{"id": u["id"], "path": u["path"], "symbol": u["symbol"],
                   "score": u["score"], "content": u["content"]} for u in tests],
        "code_files": code_files,
        "history": history,
        "todo": [
            "按规格实现 gold_plus 折扣规则",
            "运行目标测试与回归测试",
        ],
        "done": [],
    }


def render_prompt(context: dict[str, Any]) -> list[dict[str, str]]:
    spec_block = "\n\n".join(f"[{item['id']}]\n{item['content']}" for item in context["specs"])
    code_block = "\n\n".join(f"[{item['id']}]\n{item['content']}" for item in context["code"])
    test_block = "\n\n".join(f"[{item['id']}]\n{item['content']}" for item in context["tests"])
    files_block = "\n\n".join(
        f"### {path}\n```python\n{text}```" for path, text in context["code_files"].items()
    )
    history = context["history"] or "无历史状态"

    system = (
        "你是一个严格按规格修改代码的工程师。只依据给出的规格、代码和测试完成任务，"
        "不得修改测试文件，不得删除或放宽断言。"
        "输出必须是可直接 git apply 的 unified diff，放在一个 ```diff 代码块中，"
        "路径使用 a/ 与 b/ 前缀，文件路径相对于工程根目录。"
        "除代码块外不要输出任何其他内容。"
    )
    user = f"""# 任务
{context['task']}

# 适用规格
{spec_block}

# 相关代码
{code_block}

# 相关测试
{test_block}

# 需要修改的文件全文
{files_block}

# PowerContext 记录的历史状态
{history}

请给出实现该任务的 unified diff。"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
