"""Thin PowerContext client used to persist and recall task state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

from .config import Config


class PowerContextError(RuntimeError):
    pass


class PowerContext:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.records: list[dict[str, Any]] = []

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.cfg.pc_token:
            headers["Authorization"] = f"Bearer {self.cfg.pc_token}"
        return headers

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.cfg.pc_url}{path}"
        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=30)
        except requests.RequestException as exc:
            raise PowerContextError(f"PowerContext 不可用（{url}）：{exc}") from exc
        if response.status_code >= 400:
            raise PowerContextError(
                f"PowerContext {path} 返回 {response.status_code}：{response.text[:300]}"
            )
        data = response.json()
        self.records.append({"path": path, "payload": payload, "response": data})
        return data

    def get_scope(self) -> str:
        if self.cfg.pc_scope:
            return self.cfg.pc_scope
        cache = self.cfg.work_dir / "scope.json"
        if cache.is_file():
            scope_id = json.loads(cache.read_text(encoding="utf-8"))["scope_id"]
            self.cfg.pc_scope = scope_id
            return scope_id
        data = self._post(
            "/v1/scopes",
            {
                "title": "SDD context module",
                "summary": "Engineering context for spec-driven development tasks",
                "idempotency_key": "sdd-context-module",
            },
        )
        scope_id = data["scope_id"]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"scope_id": scope_id}, indent=2), encoding="utf-8")
        self.cfg.pc_scope = scope_id
        return scope_id

    def forget_previous(self, task_id: str) -> int:
        scope_id = self.get_scope()
        listed = self._post(
            "/v1/atomic-memory/list",
            {"scope_id": scope_id, "kind": "decision", "limit": 100},
        )
        prefix = f"{task_id}:"
        forgotten = 0
        for item in listed.get("items", []):
            if item.get("state") != "active" or not item["text"].startswith(prefix):
                continue
            self._post(
                "/v1/atomic-memory/lifecycle",
                {
                    "scope_id": scope_id,
                    "target": {
                        "artifact": item["artifact"],
                        "state_version": item["state_version"],
                    },
                    "state": "forgotten",
                },
            )
            forgotten += 1
        return forgotten

    def remember_run(self, task_id: str, run_id: str, summary: str, decisions: list[str]) -> None:
        scope_id = self.get_scope()
        self.forget_previous(task_id)
        self._post(
            "/v1/sources/content",
            {
                "scope_id": scope_id,
                "source_id": f"{task_id}-{run_id}",
                "content": summary,
                "metadata": {"origin": "sdd-context-module", "task_id": task_id, "run_id": run_id},
            },
        )
        for decision in decisions:
            self._post(
                "/v1/memory/remember",
                {
                    "scope_id": scope_id,
                    "kind": "decision",
                    "text": decision,
                    "reason": f"task {task_id} run {run_id}",
                },
            )

    def recall(self, query: str, max_bytes: int = 4000) -> str:
        scope_id = self.get_scope()
        data = self._post(
            "/v1/context/prepare",
            {"scope_id": scope_id, "query": query, "max_bytes": max_bytes},
        )
        return data.get("content") or ""

    def flush(self, path: Path) -> None:
        if not self.records:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for record in self.records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.records.clear()
