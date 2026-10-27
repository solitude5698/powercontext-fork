"""MiniMax chat client with a local transcript for review."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

from .config import Config


class MiniMaxError(RuntimeError):
    pass


class MiniMax:
    def __init__(self, cfg: Config, transcript: Path):
        if not cfg.minimax_api_key:
            raise MiniMaxError("未配置 MINIMAX_API_KEY，请参考 .env.example 设置")
        self.cfg = cfg
        self.transcript = transcript

    def chat(self, messages: list[dict[str, str]], *, max_tokens: int = 4000, temperature: float = 0.2) -> str:
        payload = {
            "model": self.cfg.minimax_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        started = time.time()
        response = requests.post(
            f"{self.cfg.minimax_base_url}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {self.cfg.minimax_api_key}",
                "Content-Type": "application/json",
            },
            timeout=180,
        )
        elapsed = round(time.time() - started, 2)
        if response.status_code >= 400:
            raise MiniMaxError(f"MiniMax 返回 {response.status_code}：{response.text[:400]}")
        data = response.json()
        content = data["choices"][0]["message"].get("content") or ""
        self._record(messages, payload, data, content, elapsed)
        return content

    def _record(self, messages, payload, data, content, elapsed) -> None:
        entry = {
            "request": {"model": payload["model"], "messages": messages,
                        "max_tokens": payload["max_tokens"], "temperature": payload["temperature"]},
            "response": {"content": content,
                         "usage": data.get("usage"),
                         "request_id": data.get("id")},
            "elapsed_seconds": elapsed,
        }
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        sys.stderr.write(f"[minimax] {payload['model']} 用时 {elapsed}s，输出 {len(content)} 字符\n")
