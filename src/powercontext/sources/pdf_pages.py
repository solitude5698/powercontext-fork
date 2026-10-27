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

"""Full-text PDF page capture with exact page-level evidence retrieval."""

from __future__ import annotations

import hashlib
import io
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.artifacts.models import MemoryCitation
from powercontext.builtin.runtime.models import GetMemoryEntryRequest, SearchMemoryRequest
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

PDF_PAGE_SOURCE_NAME = "pdf-page"


class PDFDocumentInput(BaseModel):
    """One complete original PDF and the identity its pages are filed under."""

    document_id: str
    content: bytes

    def model_post_init(self, __context: Any, /) -> None:
        if not isinstance(self.document_id, str) or not self.document_id.strip():
            raise ValueError("document_id must be a non-empty string")


class PDFPageInput(BaseModel):
    """One physical page of an original PDF, numbered from one."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """A captured page whose identity is document digest plus physical page."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Return the delimiter-safe identity of one physical PDF page."""

    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must be a non-empty string")
    encoded = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(encoded) <= MAX_SOURCE_ID_LENGTH:
        return encoded
    digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
    return f"pdf:{digest}:{document_sha256}:{page_number}"


def _document_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _open_reader(content: bytes) -> PdfReader:
    if not content:
        raise ValueError("empty PDF content")
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            if reader.decrypt("") == 0:
                raise ValueError("PDF requires a user password")
        pages = len(reader.pages)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("PDF content could not be parsed") from error
    if pages < 1:
        raise ValueError("PDF must contain at least one page")
    return reader


def parse_pdf_document(value: PDFDocumentInput, /) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of an original PDF in order."""

    if not isinstance(value, PDFDocumentInput):
        raise ValueError("parse_pdf_document requires a PDFDocumentInput")
    digest = _document_digest(value.content)
    reader = _open_reader(value.content)
    pages: list[PDFPageInput] = []
    try:
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text()
            pages.append(
                PDFPageInput(
                    document_id=value.document_id,
                    document_sha256=digest,
                    page_number=number,
                    text=text if isinstance(text, str) else "",
                )
            )
    except Exception as error:
        raise ValueError("PDF content could not be fully parsed") from error
    return tuple(pages)


class PDFPageSourceAdapter:
    """Resolve and read physical PDF page Sources."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=identity,
            source_id=identity,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        if not isinstance(source, PDFPageSource):
            raise TypeError("only PDF page Sources can be read as PDF pages")
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one PDF page as textual evidence with its document identity."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDFPageSourceAdapter(),
    projections=(PDFPageTextEvidenceProjection(),),
)


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Search one scoped Memory head for distinct pages answering a question."""

    page = await memory.search(
        SearchMemoryRequest(
            query=question,
            limit=max(limit * 4, 20),
            mode="auto",
        )
    )
    citations: list[MemoryCitation] = []
    seen: set[str] = set()
    for hit in page.hits:
        if len(citations) >= limit:
            break
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        record = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        page_key = next((reference.source_id for reference in record.sources), None)
        if page_key is None or page_key in seen:
            continue
        seen.add(page_key)
        citations.append(citation)
    return tuple(citations)
