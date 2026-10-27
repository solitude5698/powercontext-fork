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

"""Selected-branch capture of Pi v3 session history as immutable evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_SCHEMA_VERSION = 3
CHECKPOINT_SCHEMA = "powercontext.pi-session-checkpoint.v1"
VISIBLE_TYPES = frozenset({"message", "compaction", "branch_summary", "custom_message"})


class PiSessionInput(BaseModel):
    """One complete Pi session archive and the branch leaf to capture."""

    content: bytes
    leaf_id: str | None = None

    def model_post_init(self, __context: Any, /) -> None:
        if self.leaf_id is not None and (not self.leaf_id.strip() or self.leaf_id != self.leaf_id.strip()):
            raise ValueError("leaf_id must be a non-empty trimmed string")


class PiEventInput(BaseModel):
    """One visible event on the selected branch, retaining its raw record."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """A captured Pi event whose identity is its session and event ID."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Return the delimiter-safe identity of one Pi session event."""

    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must be a non-empty string")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id must be a non-empty string")
    identity = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"pi event source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return identity


def _compact_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif kind == "toolCall":
            name = block.get("name")
            call_id = block.get("id")
            parts.append(f"[tool_call {name} {call_id}] {_compact_json(block.get('arguments'))}")
    return "\n".join(parts)


def _message_body(message: dict[str, Any], role: str) -> str:
    if role == "toolResult":
        tool_name = message.get("toolName")
        call_id = message.get("toolCallId")
        return f"[tool_result {tool_name} {call_id}] {_content_text(message.get('content'))}"
    if role == "bashExecution":
        command = message.get("command")
        output = message.get("output")
        return f"$ {command}\n{output if isinstance(output, str) else ''}"
    return _content_text(message.get("content"))


def _summary(record: dict[str, Any]) -> str:
    summary = record.get("summary")
    return summary if isinstance(summary, str) else ""


def _event_from_record(session_id: str, record: dict[str, Any]) -> PiEventInput | None:
    record_type = record.get("type")
    if record_type not in VISIBLE_TYPES:
        return None
    if record_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if not isinstance(role, str) or not role.strip():
            return None
        body = _message_body(message, role)
    elif record_type == "compaction":
        role = "compaction"
        body = _summary(record)
    elif record_type == "branch_summary":
        role = "branch_summary"
        body = _summary(record)
    else:
        role = "custom"
        body = _content_text(record.get("content"))
    if not body.strip():
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=record["id"],
        parent_id=record.get("parentId"),
        event_type=record_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _decode_records(content: bytes) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session archive must be strictly UTF-8 encoded") from error
    records: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Pi session record {number} is not valid JSON") from error
        if not isinstance(record, dict):
            raise ValueError(f"Pi session record {number} must be a JSON object")
        records.append(record)
    return records


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Parse the complete ancestor chain of the selected leaf into visible events."""

    if not isinstance(value, PiSessionInput):
        raise ValueError("parse_pi_session requires a PiSessionInput")
    records = _decode_records(value.content)
    if not records:
        raise ValueError("Pi session archive must start with a session header")
    header = records[0]
    session_id = header.get("id")
    if (
        header.get("type") != "session"
        or header.get("version") != PI_SESSION_SCHEMA_VERSION
        or not isinstance(session_id, str)
        or not session_id.strip()
    ):
        raise ValueError("first record must be a version 3 session header with a non-empty id")

    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in records[1:]:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("every Pi record must have a non-empty id")
        if event_id in by_id:
            raise ValueError(f"duplicate Pi record id {event_id!r}")
        parent_id = record.get("parentId")
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in by_id):
            raise ValueError(f"Pi record {event_id!r} references a parent that has not appeared")
        by_id[event_id] = record
        order.append(event_id)

    if not order:
        raise ValueError("Pi session must contain at least one record")
    leaf_id = value.leaf_id if value.leaf_id is not None else order[-1]
    if leaf_id not in by_id:
        raise ValueError(f"leaf {leaf_id!r} does not exist in the Pi session")

    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = leaf_id
    while current is not None:
        if current in seen:
            raise ValueError("Pi session history must not contain a cycle")
        seen.add(current)
        record = by_id[current]
        chain.append(record)
        parent = record.get("parentId")
        current = parent if isinstance(parent, str) else None
    chain.reverse()

    events: list[PiEventInput] = []
    for record in chain:
        event = _event_from_record(session_id, record)
        if event is not None:
            events.append(event)
    return tuple(events)


def _record_digest(record: dict[str, JsonValue]) -> str:
    return "sha256:" + hashlib.sha256(_compact_json(record).encode("utf-8")).hexdigest()


def _load_checkpoint(checkpoint: JsonValue | None) -> dict[str, str]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict) or checkpoint.get("schema") != CHECKPOINT_SCHEMA:
        raise ValueError("Pi session checkpoint has an unsupported shape")
    events = checkpoint.get("events")
    if not isinstance(events, dict):
        raise ValueError("Pi session checkpoint must contain imported event digests")
    state: dict[str, str] = {}
    for identity, digest in events.items():
        if not isinstance(identity, str) or not isinstance(digest, str):
            raise ValueError("Pi session checkpoint entries must be strings")
        state[identity] = digest
    return state


def _encode_checkpoint(state: dict[str, str]) -> dict[str, JsonValue]:
    return {"schema": CHECKPOINT_SCHEMA, "events": dict(sorted(state.items()))}


class PiEventSourceAdapter:
    """Resolve and read Pi session event Sources."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        identity = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=identity,
            source_id=identity,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        if not isinstance(source, PiEventSource):
            raise TypeError("only Pi session event Sources can be read as Pi events")
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose one Pi event as textual evidence with its branch metadata."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PiEventSourceAdapter(),
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Import the selected Pi branch without duplicating durable events."""

    name = PI_SESSION_CONNECTOR_NAME
    version = "1"
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(PiSessionInput(content=self._path.read_bytes(), leaf_id=self._leaf_id))
        state = _load_checkpoint(session.checkpoint)
        for event in events:
            identity = pi_event_source_id(event.session_id, event.event_id)
            digest = _record_digest(event.record)
            imported = state.get(identity)
            if imported is not None:
                if imported != digest:
                    raise ValueError(f"already imported Pi event {event.event_id!r} was rewritten")
                continue
            outcome = await session.submit(identity, PI_EVENT_SOURCE_NAME, event)
            if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                return ConnectorRunCompletion(
                    status=ConnectorRunStatus.INCOMPLETE,
                    checkpoint=_encode_checkpoint(state),
                )
            state[identity] = digest
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=_encode_checkpoint(state))
