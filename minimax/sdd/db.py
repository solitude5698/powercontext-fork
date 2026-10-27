"""seekdb storage: engineering materials and their retrieval index."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import mysql.connector

from .config import Config

DDL = [
    """
    CREATE TABLE IF NOT EXISTS materials (
        id VARCHAR(255) NOT NULL,
        kind VARCHAR(16) NOT NULL,
        path VARCHAR(255) NOT NULL,
        symbol VARCHAR(128) NOT NULL DEFAULT '',
        title VARCHAR(255) NOT NULL DEFAULT '',
        content TEXT NOT NULL,
        PRIMARY KEY (id),
        FULLTEXT INDEX ft_content(content) WITH PARSER ngram
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        id VARCHAR(64) NOT NULL,
        task_id VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL,
        detail TEXT NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id)
    )
    """,
]

TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


class Store:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.query_log: list[dict[str, Any]] = []

    def connect(self):
        conn = mysql.connector.connect(
            host=self.cfg.seekdb_host,
            port=self.cfg.seekdb_port,
            user=self.cfg.seekdb_user,
            password=self.cfg.seekdb_password,
            connection_timeout=10,
            autocommit=True,
        )
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{self.cfg.seekdb_database}`")
        cur.close()
        conn.database = self.cfg.seekdb_database
        return conn

    def ensure_schema(self) -> None:
        conn = self.connect()
        try:
            cur = conn.cursor()
            for statement in DDL:
                cur.execute(statement)
            cur.close()
        finally:
            conn.close()

    def reset(self) -> None:
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute("TRUNCATE TABLE materials")
            cur.close()
        finally:
            conn.close()

    def upsert(self, items: Iterable[dict[str, str]]) -> int:
        rows = [
            (
                item["id"],
                item["kind"],
                item["path"],
                item.get("symbol", ""),
                item.get("title", ""),
                item["content"],
            )
            for item in items
        ]
        if not rows:
            return 0
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.executemany(
                "REPLACE INTO materials (id, kind, path, symbol, title, content) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                rows,
            )
            cur.close()
        finally:
            conn.close()
        return len(rows)

    def search(self, text: str, kinds: tuple[str, ...] = (), limit: int = 8) -> list[dict[str, Any]]:
        """Full-text search plus identifier matching, both executed in seekdb."""
        conn = self.connect()
        try:
            cur = conn.cursor(dictionary=True)
            kind_sql = ""
            params: list[Any] = [text, text]
            if kinds:
                placeholders = ",".join(["%s"] * len(kinds))
                kind_sql = f" AND kind IN ({placeholders})"
                params.extend(kinds)
            params.append(limit * 3)
            cur.execute(
                "SELECT id, kind, path, symbol, title, content, "
                " MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE) AS ft_score "
                "FROM materials "
                "WHERE MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE) > 0"
                + kind_sql
                + " ORDER BY ft_score DESC LIMIT %s",
                params,
            )
            rows = cur.fetchall()
            scores = {row["id"]: float(row["ft_score"]) for row in rows}
            by_id = {row["id"]: row for row in rows}

            for token in {t.lower() for t in TOKEN_PATTERN.findall(text)}:
                like_params: list[Any] = [f"%{token}%"]
                like_sql = "SELECT id, kind, path, symbol, title, content FROM materials WHERE LOWER(content) LIKE %s"
                if kinds:
                    placeholders = ",".join(["%s"] * len(kinds))
                    like_sql += f" AND kind IN ({placeholders})"
                    like_params.extend(kinds)
                cur.execute(like_sql, like_params)
                for row in cur.fetchall():
                    by_id.setdefault(row["id"], row)
                    scores[row["id"]] = scores.get(row["id"], 0.0) + 0.5
            cur.close()
        finally:
            conn.close()

        ranked = sorted(by_id.values(), key=lambda r: scores[r["id"]], reverse=True)[:limit]
        for row in ranked:
            row["score"] = round(scores[row["id"]], 4)
        self.query_log.append({"kind_filter": list(kinds), "query": text, "hits": len(ranked),
                               "ids": [r["id"] for r in ranked]})
        return ranked

    def record_run(self, run_id: str, task_id: str, status: str, detail: dict[str, Any]) -> None:
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "REPLACE INTO runs (id, task_id, status, detail, created_at) VALUES (%s, %s, %s, %s, %s)",
                (run_id, task_id, status, json.dumps(detail, ensure_ascii=False), datetime.now()),
            )
            cur.close()
        finally:
            conn.close()

    def flush_query_log(self, path: Path) -> None:
        if not self.query_log:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for entry in self.query_log:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.query_log.clear()
