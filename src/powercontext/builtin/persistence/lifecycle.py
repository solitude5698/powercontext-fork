# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Durable dependency graph for Source-driven availability propagation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE


class SourceLifecycleNode(BaseModel):
    """One registered evidence node and the availability last committed for it."""

    model_config = ConfigDict(frozen=True)

    scope_id: str
    node_id: str
    ordinal: int
    node_type: Literal["source", "memory", "topic"]
    parents: tuple[str, ...]
    policy: Literal["all", "any"]
    reference: dict[str, Any]
    revision: int | None = None
    sequence: int = 0
    event_digest: str | None = None
    available: bool = True


class SourceLifecycleRepository:
    """Read and replace lifecycle rows inside one caller-owned Scope."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._database = database

    async def load(self, scope_id: str, /) -> tuple[SourceLifecycleNode, ...]:
        async with self._database.transaction() as connection:
            return await self._load(connection, scope_id)

    async def save(self, nodes: Sequence[SourceLifecycleNode], /) -> None:
        if not nodes:
            return
        async with self._database.transaction() as connection:
            await self._save(connection, nodes)

    async def inactive_artifact_ids(self, scope_id: str, /) -> frozenset[str]:
        """Return registered Topic artifacts whose dependency support is withdrawn."""

        table = SOURCE_LIFECYCLE_NODES_TABLE
        async with self._database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(table.c.reference).where(
                        table.c.scope_id == scope_id,
                        table.c.node_type == "topic",
                        table.c.available.is_(False),
                    )
                )
            ).scalars()
        return frozenset(str(json.loads(row)["artifact_id"]) for row in rows)

    async def _load(self, connection: AsyncConnection, scope_id: str, /) -> tuple[SourceLifecycleNode, ...]:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        rows = (
            await connection.execute(
                select(table).where(table.c.scope_id == scope_id).order_by(table.c.ordinal)
            )
        ).mappings()
        return tuple(_decode_row(row) for row in rows)

    async def _save(self, connection: AsyncConnection, nodes: Sequence[SourceLifecycleNode], /) -> None:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        for node in nodes:
            values = _encode_row(node)
            identity = (table.c.scope_id == node.scope_id, table.c.node_id == node.node_id)
            result = await connection.execute(update(table).where(*identity).values(**values))
            if result.rowcount == 0:
                await connection.execute(insert(table).values(**values))


def _encode_row(node: SourceLifecycleNode) -> dict[str, Any]:
    return {
        "scope_id": node.scope_id,
        "node_id": node.node_id,
        "ordinal": node.ordinal,
        "node_type": node.node_type,
        "parents": _canonical_json(list(node.parents)),
        "policy": node.policy,
        "reference": _canonical_json(node.reference),
        "revision": node.revision,
        "sequence": node.sequence,
        "event_digest": node.event_digest,
        "available": node.available,
    }


def _decode_row(row: Mapping[Any, Any]) -> SourceLifecycleNode:
    return SourceLifecycleNode(
        scope_id=str(row["scope_id"]),
        node_id=str(row["node_id"]),
        ordinal=int(row["ordinal"]),
        node_type=str(row["node_type"]),
        parents=tuple(str(parent) for parent in json.loads(row["parents"])),
        policy=str(row["policy"]),
        reference=dict(json.loads(row["reference"])),
        revision=None if row["revision"] is None else int(row["revision"]),
        sequence=int(row["sequence"]),
        event_digest=None if row["event_digest"] is None else str(row["event_digest"]),
        available=bool(row["available"]),
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ["SourceLifecycleNode", "SourceLifecycleRepository"]
