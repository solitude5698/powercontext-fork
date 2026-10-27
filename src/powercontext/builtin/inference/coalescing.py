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

"""Cross-call Embedding request coalescing with a bounded vector cache."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel


@dataclass(frozen=True, slots=True)
class _Resolved:
    """One text's vector plus the batch and usage that produced it."""

    vector: EmbeddingVector
    batch: object | None
    usage: InferenceUsage | None


def _positive_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


class CoalescingEmbeddingModel:
    """Share in-flight text embeddings across calls and cache successful vectors."""

    def __init__(
        self,
        model: EmbeddingModel,
        /,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        self._model = model
        self.profile = model.profile
        self._max_batch_size = _positive_int("max_batch_size", max_batch_size)
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")
        if isinstance(cache_size, bool) or not isinstance(cache_size, int) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")
        self._coalesce_seconds = float(coalesce_ms) / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._pending: dict[str, asyncio.Future[_Resolved]] = {}
        self._queue: deque[str] = deque()
        self._task: asyncio.Task[None] | None = None

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        original = tuple(texts)
        if not original:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        unique = tuple(dict.fromkeys(original))
        loop = asyncio.get_running_loop()
        waiting: dict[str, asyncio.Future[_Resolved]] = {}
        enqueued = False
        for text in unique:
            if self._cache_size > 0:
                cached = self._cache.get(text)
                if cached is not None:
                    self._cache.move_to_end(text)
                    ready = loop.create_future()
                    ready.set_result(_Resolved(cached, None, None))
                    waiting[text] = ready
                    continue
            future = self._pending.get(text)
            if future is None:
                future = loop.create_future()
                self._pending[text] = future
                self._queue.append(text)
                enqueued = True
            waiting[text] = future
        if enqueued:
            self._ensure_dispatch()
        resolved = await asyncio.gather(*(asyncio.shield(waiting[text]) for text in unique))
        by_text = dict(zip(unique, resolved, strict=True))
        usage = InferenceUsage(requests=0)
        seen: set[object] = set()
        for item in resolved:
            if item.batch is None or item.batch in seen or item.usage is None:
                continue
            seen.add(item.batch)
            usage = InferenceUsage(
                requests=usage.requests + item.usage.requests,
                input_tokens=_add(usage.input_tokens, item.usage.input_tokens),
                output_tokens=_add(usage.output_tokens, item.usage.output_tokens),
            )
        return EmbeddingResult(vectors=tuple(by_text[text].vector for text in original), usage=usage)

    def _ensure_dispatch(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        try:
            # One shared window so concurrently arriving calls join the same batches.
            await asyncio.sleep(self._coalesce_seconds)
            while True:
                batch: list[str] = []
                while self._queue and len(batch) < self._max_batch_size:
                    text = self._queue.popleft()
                    if text in self._pending:
                        batch.append(text)
                if not batch:
                    if not self._queue:
                        return
                    continue
                await self._execute(tuple(batch))
                if not self._queue:
                    return
                await asyncio.sleep(0)
        finally:
            self._task = None

    async def _execute(self, texts: tuple[str, ...]) -> None:
        batch = object()
        try:
            result = await self._model.embed(texts)
            vectors = tuple(result.vectors)
            if len(vectors) != len(texts):
                raise ValueError("embedding vector count does not match the batch size")
        except BaseException as error:
            for text in texts:
                future = self._pending.pop(text, None)
                if future is not None and not future.done():
                    future.set_exception(error)
            return
        usage = result.usage
        for text, vector in zip(texts, vectors, strict=True):
            if self._cache_size > 0:
                self._cache[text] = vector
                self._cache.move_to_end(text)
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
            future = self._pending.pop(text, None)
            if future is not None and not future.done():
                future.set_result(_Resolved(vector, batch, usage))


def _add(total: int | None, value: int | None) -> int | None:
    if value is None:
        return total
    return (0 if total is None else total) + value


__all__ = ["CoalescingEmbeddingModel"]
