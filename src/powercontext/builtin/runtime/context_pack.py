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

"""Content-addressed context packs and prefix-reusing deltas.

A pack is a sequence of byte chunks plus a digest over the canonical JSON of its
``schema`` and Base64 ``items``. A delta names the base digest, the number of
leading base chunks the receiver may keep, and the target's remaining chunks.
Both wire forms are canonical JSON so that any receiver rebuilds the exact same
bytes without sharing sender state.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512
_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(ValueError):
    """Raised when a pack or delta cannot be built, decoded, or applied."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _digest(items: tuple[bytes, ...]) -> str:
    unsigned = {"schema": PACK_SCHEMA, "items": _encode_items(items)}
    return "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()


def _decode_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("items must be a JSON array")
    decoded: list[bytes] = []
    for encoded in value:
        if not isinstance(encoded, str):
            raise ContextPackError("items must contain Base64 strings")
        try:
            decoded.append(base64.b64decode(encoded.encode("ascii"), validate=True))
        except (binascii.Error, UnicodeEncodeError, ValueError) as error:
            raise ContextPackError("item is not valid Base64") from error
    return tuple(decoded)


def _load_object(raw: bytes, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise ContextPackError(f"{label} must be bytes")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ContextPackError(f"{label} must contain exactly {', '.join(sorted(fields))}")
    return payload


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
        raise ContextPackError(f"{label} must be a sha256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ContextPack:
    """Deterministically chunked context text with a content digest."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: Iterable[bytes]) -> ContextPack:
        normalized = tuple(items)
        for item in normalized:
            if not isinstance(item, bytes):
                raise ContextPackError("pack items must be bytes")
        digest = _digest(normalized)
        encoded = _encode_items(normalized)
        count = 0
        while True:
            resolved = len(_canonical({"schema": PACK_SCHEMA, "items": encoded, "digest": digest, "byte_count": count}))
            if resolved == count:
                break
            count = resolved
        return cls(normalized, digest, count)

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        if not isinstance(content, str):
            raise ContextPackError("content must be str")
        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")
        chunks: list[bytes] = []
        pending = bytearray()
        for character in content:
            encoded = character.encode("utf-8")
            if len(encoded) > chunk_bytes:
                raise ContextPackError("chunk_bytes cannot hold one UTF-8 character")
            if len(pending) + len(encoded) > chunk_bytes:
                chunks.append(bytes(pending))
                pending = bytearray()
            pending.extend(encoded)
        if pending:
            chunks.append(bytes(pending))
        return cls.build(chunks)

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPack:
        payload = _load_object(raw, _PACK_FIELDS, "context pack")
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError(f"context pack schema must be {PACK_SCHEMA!r}")
        items = _decode_items(payload["items"])
        digest = _require_digest(payload["digest"], "context pack digest")
        if digest != _digest(items):
            raise ContextPackError("context pack digest does not match its items")
        byte_count = payload["byte_count"]
        if type(byte_count) is not int or byte_count != len(raw):
            raise ContextPackError("context pack byte_count does not match its wire bytes")
        return cls(items, digest, byte_count)

    def to_bytes(self) -> bytes:
        return _canonical(
            {
                "schema": PACK_SCHEMA,
                "items": _encode_items(self.items),
                "digest": self.digest,
                "byte_count": self.byte_count,
            }
        )


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """Reuse of a base pack's leading chunks plus the target's remaining chunks."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("between requires two context packs")
        prefix = 0
        limit = min(len(base.items), len(target.items))
        while prefix < limit and base.items[prefix] == target.items[prefix]:
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPackDelta:
        payload = _load_object(raw, _DELTA_FIELDS, "context pack delta")
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError(f"context pack delta schema must be {DELTA_SCHEMA!r}")
        prefix = payload["prefix_items"]
        if type(prefix) is not int or prefix < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        return cls(
            _require_digest(payload["base_digest"], "base digest"),
            _require_digest(payload["target_digest"], "target digest"),
            prefix,
            _decode_items(payload["appended"]),
        )

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _canonical(
            {
                "schema": DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": _encode_items(self.appended),
            }
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("apply requires a context pack and a context pack delta")
    if base.digest != delta.base_digest:
        raise ContextPackError("delta base digest does not match the supplied base pack")
    if type(delta.prefix_items) is not int or not 0 <= delta.prefix_items <= len(base.items):
        raise ContextPackError("delta prefix_items is outside the base pack")
    rebuilt = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if rebuilt.digest != delta.target_digest:
        raise ContextPackError("rebuilt pack does not match the delta target digest")
    return rebuilt
