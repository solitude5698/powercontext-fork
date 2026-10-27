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
import math
import re
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.artifacts.models import MemoryCitation
from powercontext.builtin.runtime.models import MemoryEntryRecord
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


_TOKEN_PATTERN = re.compile(r"[a-z]+|\d+")
_STOPWORDS = frozenset(
    """a about above after again against all also am an and any are as at be because been before
    being below between both but by can could did do does doing down during each few for from
    further had has have having he her here hers herself him himself his how i if in into is it
    its itself just me more most my myself no nor not of off on once only or other our ours
    ourselves out over own same she should so some such than that the their theirs them
    themselves then there these they this those through to too under until up very was we were
    what when where which while who whom why will with would you your yours yourself yourselves
    company pepsico fy fiscal year report""".split()
)
_HEADER_SKIP = frozenset("of the and to in for".split())


def _root(token: str) -> str:
    """Cheap surface folding so inflected question words still match page text."""

    if token.isdigit():
        return token
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token[:6]


def _phrase_roots(phrase: str) -> tuple[str, ...]:
    return tuple(_root(token) for token in _TOKEN_PATTERN.findall(phrase.lower()))


def _content_roots(pattern: tuple[str, ...]) -> tuple[str, ...]:
    """Drop filler words so titles and intents compare against heading text."""

    return tuple(token for token in pattern if token not in _HEADER_SKIP and len(token) > 1)


def _tokens(text: str) -> list[str]:
    return [_root(token) for token in _TOKEN_PATTERN.findall(text.lower())]


def _query_terms(question: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_PATTERN.findall(question.lower()):
        if token in _STOPWORDS or len(token) < 2:
            continue
        value = _root(token)
        if value not in seen:
            seen.add(value)
            terms.append(value)
    return terms


# Question intents that 10-K prose answers with different vocabulary than the question uses.
_CONCEPTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("geography", "geographies", "region", "regions", "country", "countries", "market", "markets"),
        (
            "geographic segment",
            "north america",
            "europe",
            "asia pacific",
            "latin america",
            "africa",
            "middle east",
            "amesa",
        ),
    ),
    (
        ("legal", "litigation", "lawsuit", "lawsuits", "battle", "battles", "proceeding", "proceedings"),
        ("legal proceedings", "litigation", "claims", "regulatory", "investigations", "contingencies"),
    ),
    (
        ("restructuring", "impairment", "productivity", "severance"),
        ("restructuring", "impairment", "productivity plan", "charges", "severance"),
    ),
    (
        ("employee", "employees", "headcount", "workforce", "personnel"),
        ("employees", "personnel", "workforce", "headcount"),
    ),
    (
        ("dividend", "dividends", "buyback", "repurchase", "repurchases"),
        ("dividends", "share repurchase", "common stock"),
    ),
    (
        ("acquisition", "acquisitions", "divestiture", "divestitures", "merger", "mergers"),
        ("acquisitions", "divestitures", "business combination", "goodwill"),
    ),
    (
        ("debt", "borrowing", "borrowings", "leverage"),
        ("long-term debt", "short-term borrowings", "notes", "interest expense"),
    ),
    (
        ("liquidity", "solvency", "working", "capital"),
        ("working capital", "current assets", "current liabilities", "cash and cash equivalents"),
    ),
    (
        ("customer", "customers", "client", "clients"),
        ("customers", "retailers", "distributors", "licensees"),
    ),
    (
        ("product", "products", "service", "services", "brand", "brands"),
        ("products", "services", "brands", "beverages", "snacks"),
    ),
    (
        ("competition", "competitor", "competitors", "competitive"),
        ("competition", "competitors", "competitive"),
    ),
    (
        ("margin", "margins", "profitability", "profit"),
        ("gross profit", "operating profit", "net income", "gross margin"),
    ),
    (
        ("revenue", "revenues", "sales", "growth"),
        ("net revenue", "net sales", "growth"),
    ),
    (
        ("tax", "taxes", "rate"),
        ("effective tax rate", "income taxes", "provision for income taxes"),
    ),
    (
        ("pension", "pensions", "retirement"),
        ("pension", "retiree medical", "benefit obligations"),
    ),
    (
        ("executive", "executives", "officer", "officers", "ceo", "cfo", "director", "directors"),
        ("executive officers", "board of directors", "chief executive officer"),
    ),
    (
        ("risk", "risks", "uncertainty", "uncertainties"),
        ("risk factors", "uncertainties", "adverse effect"),
    ),
    (
        ("regulation", "regulations", "regulatory", "compliance"),
        ("regulatory", "government regulation", "compliance"),
    ),
    (
        ("inventory", "inventories", "turnover"),
        ("inventories", "cost of sales", "inventory turnover"),
    ),
    (
        ("capex", "capital", "expenditure", "expenditures"),
        ("capital expenditures", "property plant and equipment"),
    ),
)

_TRIGGERS: dict[str, tuple[str, ...]] = {}
for _trigger_words, _expansion_words in _CONCEPTS:
    _expanded: list[str] = []
    for _phrase in _expansion_words:
        for _value in _phrase_roots(_phrase):
            if _value not in _expanded:
                _expanded.append(_value)
    for _word in _trigger_words:
        _TRIGGERS.setdefault(_root(_word), tuple(_expanded))

# Adjacent word pairs whose presence marks a page that answers the question's intent.
_PHRASE_HINTS: tuple[tuple[str, str], ...] = (
    ("legal", "legal proceedings"),
    ("legal", "legal litigation"),
    ("litigation", "legal proceedings"),
    ("battle", "litigation claims"),
    ("geography", "north america"),
    ("geography", "latin america"),
    ("ebitda", "income statement"),
    ("ebitda", "statement of cash flows"),
    ("margin", "income statement"),
    ("capex", "cash flow statement"),
    ("restructuring", "restructuring impairment"),
    ("employee", "employees personnel"),
)
_PHRASE_HINTS_ROOTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = tuple(
    (_root(trigger), _phrase_roots(phrase), _phrase_roots(phrase)[::-1])
    for trigger, phrase in _PHRASE_HINTS
)

# Statement titles are the authoritative page for questions that name a financial statement.
_STATEMENT_TITLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("income", _content_roots(_phrase_roots("consolidated statement of income"))),
    ("cash", _content_roots(_phrase_roots("consolidated statement of cash flows"))),
    ("balance", _content_roots(_phrase_roots("consolidated balance sheet"))),
    ("equity", _content_roots(_phrase_roots("consolidated statement of equity"))),
    ("comprehensive", _content_roots(_phrase_roots("consolidated statement of comprehensive income"))),
)
_STATEMENT_TRIGGERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("income", _content_roots(_phrase_roots("income statement"))),
    ("cash", _content_roots(_phrase_roots("cash flow statement"))),
    ("balance", _content_roots(_phrase_roots("balance sheet"))),
    ("equity", _content_roots(_phrase_roots("statement of equity"))),
    ("comprehensive", _content_roots(_phrase_roots("comprehensive income"))),
)

_BM25_K1 = 1.2
_BM25_B = 0.75
_EXPANSION_WEIGHT = 0.55
_PHRASE_BONUS = 3.0
_STATEMENT_BONUS = 10.0


def _page_number(source_id: str) -> int | None:
    try:
        return int(source_id.rsplit(":", 1)[-1])
    except ValueError:
        return None


def _statement_title(tokens: list[str]) -> str | None:
    head = [token for token in tokens[:60] if token not in _HEADER_SKIP]
    for name, pattern in _STATEMENT_TITLES:
        span = len(pattern)
        for start in range(len(head) - span + 1):
            if tuple(head[start : start + span]) == pattern:
                return name
    return None


def _statement_intents(terms: set[str]) -> set[str]:
    intents: set[str] = set()
    for name, pattern in _STATEMENT_TRIGGERS:
        required = {value for value in pattern if value not in _HEADER_SKIP}
        if required and required <= terms:
            intents.add(name)
    return intents


class _PageIndex:
    """Local BM25 over every stored page, reranked with report-aware signals."""

    def __init__(self, entries: tuple[MemoryEntryRecord, ...]):
        self.citations: list[MemoryCitation] = []
        self.page_keys: list[str] = []
        self.page_numbers: list[int | None] = []
        self.titles: list[str | None] = []
        self.counts: list[dict[str, int]] = []
        self.tokens: list[list[str]] = []
        self.document_frequency: dict[str, int] = {}
        for record in entries:
            if record.state != "active" or not record.entry.text.strip():
                continue
            page_ref = next(
                (ref for ref in record.entry.sources if ref.source_type == PDF_PAGE_SOURCE_NAME),
                None,
            )
            words = _tokens(record.entry.text)
            if not words:
                continue
            counts: dict[str, int] = {}
            for word in words:
                counts[word] = counts.get(word, 0) + 1
            for word in counts:
                self.document_frequency[word] = self.document_frequency.get(word, 0) + 1
            self.citations.append(record.citation)
            self.page_keys.append(page_ref.source_id if page_ref is not None else record.entry.entry_id)
            self.page_numbers.append(_page_number(page_ref.source_id) if page_ref is not None else None)
            self.titles.append(_statement_title(words))
            self.counts.append(counts)
            self.tokens.append(words)
        total = sum(len(words) for words in self.tokens)
        self.average_length = total / len(self.tokens) if self.tokens else 0.0

    def _has_phrase(self, page: int, pattern: tuple[str, ...]) -> bool:
        sequence = self.tokens[page]
        span = len(pattern)
        for start in range(len(sequence) - span + 1):
            if tuple(sequence[start : start + span]) == pattern:
                return True
        return False

    def rank(self, question: str, limit: int) -> tuple[MemoryCitation, ...]:
        base = _query_terms(question)
        if not base or not self.tokens:
            return ()
        weighted: dict[str, float] = {term: 1.0 for term in base}
        for term in base:
            for expanded in _TRIGGERS.get(term, ()):
                if expanded not in weighted:
                    weighted[expanded] = _EXPANSION_WEIGHT
        intents = _statement_intents(set(base))
        hints = [(pattern, backward) for trigger, pattern, backward in _PHRASE_HINTS_ROOTS if trigger in weighted]
        total = len(self.tokens)
        scored: list[tuple[float, int, int]] = []
        for page in range(total):
            counts = self.counts[page]
            length = len(self.tokens[page])
            norm = _BM25_K1 * (1.0 - _BM25_B + _BM25_B * length / (self.average_length or 1.0))
            score = 0.0
            matched = False
            for term, weight in weighted.items():
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                matched = True
                document_frequency = self.document_frequency.get(term, 0)
                idf = math.log(1.0 + (total - document_frequency + 0.5) / (document_frequency + 0.5))
                score += weight * idf * frequency * (_BM25_K1 + 1.0) / (frequency + norm)
            if not matched:
                continue
            for pattern, backward in hints:
                if self._has_phrase(page, pattern) or self._has_phrase(page, backward):
                    score += _PHRASE_BONUS
                    break
            if intents and self.titles[page] in intents:
                score += _STATEMENT_BONUS
            if score > 0.0:
                scored.append(
                    (score, self.page_numbers[page] if self.page_numbers[page] is not None else page, page)
                )
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected: list[MemoryCitation] = []
        seen: set[str] = set()
        for _, _, page in scored:
            key = self.page_keys[page]
            if key in seen:
                continue
            seen.add(key)
            selected.append(self.citations[page])
            if len(selected) >= limit:
                break
        return tuple(selected)


_INDEX_CACHE: dict[tuple[str, int], _PageIndex] = {}


async def _page_index(memory) -> _PageIndex | None:
    page = await memory.list()
    memory_ref = page.memory_ref
    if memory_ref is None:
        return None
    key = (memory_ref.artifact_id, memory_ref.revision)
    index = _INDEX_CACHE.get(key)
    if index is None:
        index = _PageIndex(page.entries)
        if len(_INDEX_CACHE) >= 8:
            _INDEX_CACHE.clear()
        _INDEX_CACHE[key] = index
    return index


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Rank every stored page of one scoped Memory and cite the best distinct pages."""

    if limit <= 0:
        return ()
    index = await _page_index(memory)
    if index is None:
        return ()
    return index.rank(question, limit)
