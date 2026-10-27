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

"""Propagate Source withdrawal through dependent Memory and Topic Memory nodes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext.artifacts import ArtifactRef
from powercontext.artifacts.models import MemoryCitation
from powercontext.builtin.artifacts.memory import Memory, MemoryEntryVersion, MemoryService
from powercontext.builtin.artifacts.topic_memory import TopicMemory
from powercontext.builtin.persistence.lifecycle import SourceLifecycleNode
from powercontext.builtin.records import BaseValueNotFoundError
from powercontext.builtin.sources import validate_scope_id
from powercontext.builtin.statistics import ModelUsagePurpose
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

_MAX_NODE_ID_LENGTH = 128
_POLICIES = ("all", "any")
_PURPOSES = ("generation", "audit")
_TRANSITION_REASON = "source-lifecycle"


class SourceLifecycleError(ValueError):
    """Reject a lifecycle request that violates the registered dependency graph."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"source lifecycle rejected: {code}")


class SourceLifecycleEvent(BaseModel):
    """One caller-supplied availability change for a registered Source."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    node_id: str
    revision: int = Field(gt=0)
    sequence: int = Field(gt=0)
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class ScopedSourceLifecycleService:
    """Register evidence nodes and apply Source events inside one Scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = scope_id

    async def register_source(self, node_id: str, source_ref: SourceRef, revision: int) -> None:
        node = _require_node_id(node_id)
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("revision")
        reference = source_ref.model_dump(mode="json")
        async with self._runtime._scope_operation(self.scope_id):
            try:
                await self._runtime._records().get_source(
                    self.scope_id,
                    source_ref.source_type,
                    source_ref.source_id,
                )
            except BaseValueNotFoundError:
                raise SourceLifecycleError("reference") from None
            async with self._runtime._locked(self.scope_id):
                nodes = await self._nodes()
                existing = _nodes_by_id(nodes).get(node)
                if existing is not None:
                    if (
                        existing.node_type == "source"
                        and existing.reference == reference
                        and existing.revision == revision
                    ):
                        return
                    raise SourceLifecycleError("identity")
                await self._insert(
                    nodes,
                    node=node,
                    node_type="source",
                    parents=(),
                    policy="all",
                    reference=reference,
                    revision=revision,
                    available=True,
                )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        parents: Sequence[str] = (),
        policy: str = "all",
    ) -> None:
        node = _require_node_id(node_id)
        declared = _require_parents(node, parents, policy)
        reference = citation.model_dump(mode="json")
        async with self._runtime._context(self.scope_id) as context:
            await self._require_memory_citation(context.artifacts.memory, context.artifacts.memory_artifact_id, citation)
            async with self._runtime._locked(self.scope_id):
                nodes = await self._nodes()
                existing = _nodes_by_id(nodes).get(node)
                if existing is not None:
                    if (
                        existing.node_type == "memory"
                        and existing.reference == reference
                        and existing.parents == declared
                        and existing.policy == policy
                    ):
                        return
                    raise SourceLifecycleError("identity")
                active = self._relevant(nodes, declared)
                available = _evaluate(active, declared, policy)
                created = await self._insert(
                    nodes,
                    node=node,
                    node_type="memory",
                    parents=declared,
                    policy=policy,
                    reference=reference,
                    revision=None,
                    available=available,
                )
                if not available:
                    await self._sync_memory(context.artifacts.memory, context.artifacts.memory_artifact_id, (created,))

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        parents: Sequence[str] = (),
        policy: str = "all",
    ) -> None:
        node = _require_node_id(node_id)
        declared = _require_parents(node, parents, policy)
        if artifact_ref.family != TopicMemory.family:
            raise SourceLifecycleError("topic-family")
        reference = artifact_ref.model_dump(mode="json")
        async with self._runtime._scope_operation(self.scope_id):
            try:
                await self._runtime._records().get_artifact_revision(
                    self.scope_id,
                    artifact_ref.family,
                    artifact_ref.artifact_id,
                    artifact_ref.revision,
                )
            except BaseValueNotFoundError:
                raise SourceLifecycleError("reference") from None
            async with self._runtime._locked(self.scope_id):
                nodes = await self._nodes()
                existing = _nodes_by_id(nodes).get(node)
                if existing is not None:
                    if (
                        existing.node_type == "topic"
                        and existing.reference == reference
                        and existing.parents == declared
                        and existing.policy == policy
                    ):
                        return
                    raise SourceLifecycleError("identity")
                active = self._relevant(nodes, declared)
                await self._insert(
                    nodes,
                    node=node,
                    node_type="topic",
                    parents=declared,
                    policy=policy,
                    reference=reference,
                    revision=None,
                    available=_evaluate(active, declared, policy),
                )

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        """Apply one event, propagate it to every descendant, and list active nodes."""

        async with self._runtime._context(
            self.scope_id,
            embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING,
        ) as context, self._runtime._locked(self.scope_id):
            nodes = await self._nodes()
            by_id = _nodes_by_id(nodes)
            source = by_id.get(event.node_id)
            if source is None or source.node_type != "source":
                raise SourceLifecycleError("source")
            if source.revision != event.revision:
                raise SourceLifecycleError("revision")
            digest = _event_digest(event)
            if event.sequence < source.sequence:
                raise SourceLifecycleError("sequence")
            if event.sequence == source.sequence:
                if digest != source.event_digest:
                    raise SourceLifecycleError("sequence")
                return sorted(node_id for node_id, node in by_id.items() if node.available)
            target = _propagate(nodes, event.node_id, event.available)
            await self._sync_memory(context.artifacts.memory, context.artifacts.memory_artifact_id, nodes, target)
            changed = [
                node.model_copy(
                    update={
                        "available": event.available,
                        "sequence": event.sequence,
                        "event_digest": digest,
                    }
                )
                if node.node_id == event.node_id
                else node.model_copy(update={"available": target[node.node_id]})
                for node in nodes
                if node.node_id == event.node_id or target[node.node_id] != node.available
            ]
            await self._repository.save(tuple(changed))
            return sorted(node_id for node_id, available in target.items() if available)

    async def resolve(
        self,
        node_id: str,
        *,
        purpose: str = "generation",
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        node = _require_node_id(node_id)
        if purpose not in _PURPOSES:
            raise SourceLifecycleError("purpose")
        async with self._runtime._scope_operation(self.scope_id):
            existing = _nodes_by_id(await self._nodes()).get(node)
        if existing is None:
            raise SourceLifecycleError("node")
        if purpose == "generation" and not existing.available:
            raise SourceLifecycleError("unavailable")
        return _reference(existing)

    @property
    def _repository(self):
        repository = self._runtime._source_lifecycle
        if repository is None:
            raise SourceLifecycleError("unavailable")
        return repository

    async def _nodes(self) -> tuple[SourceLifecycleNode, ...]:
        return await self._repository.load(self.scope_id)

    async def _insert(
        self,
        nodes: Sequence[SourceLifecycleNode],
        *,
        node: str,
        node_type: Literal["source", "memory", "topic"],
        parents: tuple[str, ...],
        policy: str,
        reference: dict[str, object],
        revision: int | None,
        available: bool,
    ) -> SourceLifecycleNode:
        created = SourceLifecycleNode(
            scope_id=self.scope_id,
            node_id=node,
            ordinal=max((item.ordinal for item in nodes), default=0) + 1,
            node_type=node_type,
            parents=parents,
            policy=policy,
            reference=reference,
            revision=revision,
            sequence=0,
            event_digest=None,
            available=available,
        )
        await self._repository.save((created,))
        return created

    def _relevant(
        self,
        nodes: Sequence[SourceLifecycleNode],
        parents: tuple[str, ...],
    ) -> dict[str, bool]:
        by_id = _nodes_by_id(nodes)
        missing = [parent for parent in parents if parent not in by_id]
        if missing:
            raise SourceLifecycleError("parent")
        return {parent: by_id[parent].available for parent in parents}

    async def _require_memory_citation(
        self,
        service: MemoryService,
        memory_artifact_id: str,
        citation: MemoryCitation,
    ) -> None:
        if citation.memory_ref.family != Memory.family or citation.memory_ref.artifact_id != memory_artifact_id:
            raise SourceLifecycleError("reference")
        try:
            memory = await service.revision(citation.memory_ref)
        except ArtifactNotFoundError:
            raise SourceLifecycleError("reference") from None
        if not any(
            item.entry_id == citation.entry_id and item.entry_version_id == citation.entry_version_id
            for item in memory.content.manifest.entries
        ):
            raise SourceLifecycleError("reference")

    async def _sync_memory(
        self,
        service: MemoryService,
        memory_artifact_id: str,
        nodes: Sequence[SourceLifecycleNode],
        target: dict[str, bool] | None = None,
    ) -> None:
        """Make native entry states match the propagated availability."""

        memory_nodes = [node for node in nodes if node.node_type == "memory"]
        if not memory_nodes:
            return
        try:
            memory = await service.head(memory_artifact_id)
        except ArtifactNotFoundError:
            return
        manifest = {item.entry_id: item for item in memory.content.manifest.entries}
        versions: dict[str, MemoryEntryVersion] = {
            entry.entry_id: entry for entry in await service.entries(memory)
        }
        deactivate: list[MemoryEntryVersion] = []
        reactivate: list[MemoryEntryVersion] = []
        for node in memory_nodes:
            entry_id = str(node.reference.get("entry_id", ""))
            item = manifest.get(entry_id)
            version = versions.get(entry_id)
            if item is None or version is None:
                continue
            available = node.available if target is None else target[node.node_id]
            if item.state == "active" and not available:
                deactivate.append(version)
            elif item.state != "active" and available:
                reactivate.append(version)
        if deactivate:
            memory = await service.forget(memory, entries=tuple(deactivate), reason=_TRANSITION_REASON)
        if reactivate:
            await service.reactivate(memory, entries=tuple(reactivate), reason=_TRANSITION_REASON)


class SourceLifecycleApplication:
    """Select the scoped lifecycle service over one composed Runtime."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycleService:
        return ScopedSourceLifecycleService(self._runtime, validate_scope_id(scope_id))


def _require_node_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise SourceLifecycleError("node")
    if len(value) > _MAX_NODE_ID_LENGTH:
        raise SourceLifecycleError("node")
    return value


def _require_parents(node: str, parents: object, policy: object) -> tuple[str, ...]:
    if not isinstance(parents, Sequence) or isinstance(parents, (str, bytes)):
        raise SourceLifecycleError("parent")
    declared: list[str] = []
    for parent in parents:
        candidate = _require_node_id(parent)
        if candidate == node or candidate in declared:
            raise SourceLifecycleError("parent")
        declared.append(candidate)
    if not declared:
        raise SourceLifecycleError("parent")
    if policy not in _POLICIES:
        raise SourceLifecycleError("policy")
    return tuple(declared)


def _nodes_by_id(nodes: Sequence[SourceLifecycleNode]) -> dict[str, SourceLifecycleNode]:
    return {node.node_id: node for node in nodes}


def _evaluate(active: dict[str, bool], parents: tuple[str, ...], policy: str) -> bool:
    support = [active[parent] for parent in parents]
    return all(support) if policy == "all" else any(support)


def _propagate(
    nodes: Sequence[SourceLifecycleNode],
    source_id: str,
    available: bool,
) -> dict[str, bool]:
    """Recompute availability once in registration order, which is topological."""

    states = {node.node_id: node.available for node in nodes}
    states[source_id] = available
    for node in nodes:
        if node.node_type == "source":
            continue
        states[node.node_id] = _evaluate(states, node.parents, node.policy)
    return states


def _reference(node: SourceLifecycleNode) -> SourceRef | MemoryCitation | ArtifactRef:
    if node.node_type == "source":
        return SourceRef.model_validate(node.reference)
    if node.node_type == "memory":
        return MemoryCitation.model_validate(node.reference)
    return ArtifactRef.model_validate(node.reference)


def _event_digest(event: SourceLifecycleEvent) -> str:
    payload = json.dumps(
        {
            "node_id": event.node_id,
            "revision": event.revision,
            "sequence": event.sequence,
            "available": event.available,
            "reason": event.reason,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


__all__ = ["ScopedSourceLifecycleService", "SourceLifecycleApplication", "SourceLifecycleError", "SourceLifecycleEvent"]
