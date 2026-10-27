"""Configuration for the SDD context module."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Config:
    seekdb_host: str = "127.0.0.1"
    seekdb_port: int = 2882
    seekdb_user: str = "root"
    seekdb_password: str = ""
    seekdb_database: str = "sdd_context"
    pc_url: str = "http://127.0.0.1:8000"
    pc_token: str = ""
    pc_scope: str = ""
    minimax_base_url: str = "https://api.minimaxi.com/v1"
    minimax_api_key: str = ""
    minimax_model: str = "MiniMax-M3"
    project_dir: Path = field(default_factory=lambda: ROOT / "demo_project")
    evidence_dir: Path = field(default_factory=lambda: ROOT / "evidence")
    work_dir: Path = field(default_factory=lambda: ROOT / ".work")

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Config":
        if env_file is not None:
            load_env_file(env_file)
        return cls(
            seekdb_host=os.getenv("SEEKDB_HOST", cls.seekdb_host),
            seekdb_port=int(os.getenv("SEEKDB_PORT", "2882")),
            seekdb_user=os.getenv("SEEKDB_USER", "root"),
            seekdb_password=os.getenv("SEEKDB_PASSWORD", ""),
            seekdb_database=os.getenv("SEEKDB_DATABASE", "sdd_context"),
            pc_url=os.getenv("POWERCONTEXT_URL", cls.pc_url).rstrip("/"),
            pc_token=os.getenv("POWERCONTEXT_TOKEN", ""),
            pc_scope=os.getenv("POWERCONTEXT_SCOPE", ""),
            minimax_base_url=os.getenv("MINIMAX_BASE_URL", cls.minimax_base_url).rstrip("/"),
            minimax_api_key=os.getenv("MINIMAX_API_KEY", ""),
            minimax_model=os.getenv("MINIMAX_MODEL", cls.minimax_model),
        )

    def evidence_for(self, task_id: str) -> Path:
        path = self.evidence_dir / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path
