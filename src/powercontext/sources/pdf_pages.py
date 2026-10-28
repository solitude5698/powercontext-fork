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
import sys
from typing import Any
from urllib.parse import quote, unquote

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
    how much many""".split()
)


def _root(token: str) -> str:
    """Cheap surface folding so inflected question words still match page text."""

    if token.isdigit():
        return token
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return sys.intern(token[:6])


def _fold(phrase: str) -> tuple[str, ...]:
    return tuple(_root(token) for token in _TOKEN_PATTERN.findall(phrase.lower()))


def _tokens(text: str) -> list[str]:
    return [_root(token) for token in _TOKEN_PATTERN.findall(text.lower())]


_INSTRUCTION_TAIL = re.compile(
    r"\bif\b[^.?]*\bnot (?:a |an )?(?:useful|relevant|appropriate|meaningful|standard) (?:metric|measure)\b.*$"
    r"|\bplease (?:state|provide|explain|note|justify|show|calculate|round|use|assume|respond)\b.*$"
    r"|\banswer (?:in|with|using)\b.*$"
    r"|\brespond (?:in|with|using)\b.*$"
    r"|\bround (?:to|your)\b.*$"
    r"|\bshow your work\b.*$"
    r"|\bmake sure\b.*$",
    re.IGNORECASE,
)


def _query_terms(question: str) -> list[str]:
    core = _INSTRUCTION_TAIL.sub("", question)
    if len(_TOKEN_PATTERN.findall(core)) >= 2:
        question = core
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


# Generic filing vocabulary: the words a question uses versus the words a 10-K uses for
# the same thing. Triggers are matched against folded question terms.
_CONCEPTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("geograph", "geographi", "region", "countri", "market"),
        (
            "north america",
            "latin america",
            "europe",
            "asia pacific",
            "middle east",
            "africa",
            "geographic information",
            "international operations",
            "united states",
        ),
    ),
    (
        ("legal", "litigat", "lawsuit", "battl", "proceed", "regulatori", "investigat"),
        (
            "legal proceedings",
            "litigation",
            "claims",
            "regulatory matters",
            "contingencies",
            "investigations",
        ),
    ),
    (
        ("restru", "impair", "sever", "productivity"),
        ("restructuring", "impairment", "severance", "productivity plan", "charges"),
    ),
    (
        ("capex", "capital", "expenditur"),
        (
            "capital expenditures",
            "property plant and equipment",
            "investing activities",
            "purchases of property",
        ),
    ),
    (
        ("ebitda", "operating", "operat"),
        (
            "operating income",
            "depreciation and amortization",
            "income statement",
            "operating profit",
        ),
    ),
    (
        ("margin", "profitabl", "gross"),
        ("gross margin", "gross profit", "operating margin", "operating income", "cost of sales"),
    ),
    (
        ("quick", "ratio", "liquidity", "solvenc"),
        (
            "current assets",
            "current liabilities",
            "cash and cash equivalents",
            "short-term borrowings",
            "working capital",
        ),
    ),
    (
        ("acquisit", "divest", "merger"),
        ("acquisitions", "business combinations", "divestitures", "goodwill", "purchase price"),
    ),
    (
        ("dividend", "buyback", "repurchas"),
        ("dividends", "share repurchases", "common stock", "dividend declarations"),
    ),
    (
        ("tax", "effective"),
        (
            "effective tax rate",
            "income taxes",
            "provision for income taxes",
            "statutory",
            "tax expense",
        ),
    ),
    (
        ("customer", "client", "concentrat"),
        ("customers", "concentration", "largest customer", "distributors", "credit risk"),
    ),
    (
        ("segment",),
        ("reportable segment", "business segment", "segment information", "operating segments"),
    ),
    (
        ("cyclical", "cyclicality", "seasonal"),
        ("cyclical", "economic conditions", "seasonality", "industry conditions"),
    ),
    (
        ("product", "servic", "offering", "brand", "categori"),
        ("products", "services", "brands", "product categories", "offerings"),
    ),
    (
        ("debt", "borrow", "liabilit", "leverage"),
        (
            "long-term debt",
            "short-term borrowings",
            "notes payable",
            "total liabilities",
            "interest expense",
        ),
    ),
    (
        ("employe", "headcount", "workforc", "personnel"),
        ("employees", "personnel", "workforce", "headcount"),
    ),
    (
        ("drove", "driver", "change", "increas", "decreas"),
        ("primarily due to", "increase in", "decrease in", "compared to", "higher", "lower"),
    ),
    (
        ("retain", "retention", "churn"),
        ("retention", "card member", "loyalty", "attrition", "customer retention"),
    ),
    (
        ("production", "forecast", "backlog", "rate"),
        ("production rate", "backlog", "deliveries", "orders", "guidance"),
    ),
)

_EXPANSIONS: tuple[tuple[frozenset[str], tuple[tuple[str, ...], ...]], ...] = tuple(
    (
        frozenset(token for phrase in triggers for token in _fold(phrase)),
        tuple(pattern for pattern in (_fold(phrase) for phrase in expansions) if pattern),
    )
    for triggers, expansions in _CONCEPTS
)

# Statement titles mark the pages that carry reported figures.
_STATEMENT_HEADS: tuple[tuple[str, ...], ...] = (
    ("consol", "income"),
    ("consol", "operat"),
    ("consol", "balanc"),
    ("consol", "cash"),
    ("consol", "equiti"),
    ("consol", "compre"),
    ("statem", "financ", "positi"),
)
_STATEMENT_KINDS: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...] = (
    (
        "income",
        (
            ("consol", "income"),
            ("consol", "operat"),
            ("statem", "income"),
            ("statem", "operat"),
            ("summar", "financ"),
        ),
    ),
    (
        "balance",
        (("consol", "balanc"), ("statem", "financ", "positi"), ("financ", "positi")),
    ),
    ("cash", (("consol", "cash"), ("statem", "cash"), ("cash", "flow"))),
)
_STATEMENT_INTENTS: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "income",
        frozenset(
            "margin profit income earn ebitda revenu sale expens cost operat gross tax".split()
        ),
    ),
    (
        "balance",
        frozenset(
            "ratio liquid quick current asset liabilit debt inventori receiv cash balanc".split()
        ),
    ),
    (
        "cash",
        frozenset(
            "capex capital expenditur invest financ dividend repurchas depreci amorti flow".split()
        ),
    ),
)

_BM25_K1 = 1.2
_BM25_B = 0.3
_EXPANSION_WEIGHT = 1.0
_HEADING_WEIGHT = 4.0
_STATEMENT_WEIGHT = 1.5
_INTENT_WEIGHT = 2.0
_HEADING_WIDTH = 24
_PASSAGE_WEIGHT = 0.5
_PASSAGE_WIDTH = 60
_COMMON_TERM_RATIO = 0.35


def _is_statement(head: list[str]) -> bool:
    if head.count("consol") >= 3:
        return False
    return any(all(token in head for token in pattern) for pattern in _STATEMENT_HEADS)


def _statement_kinds(head: list[str]) -> frozenset[str]:
    if head.count("consol") >= 3:
        return frozenset()
    return frozenset(
        kind
        for kind, patterns in _STATEMENT_KINDS
        if any(all(token in head for token in pattern) for pattern in patterns)
    )


def _document_of(source_id: str) -> str:
    body = source_id[4:] if source_id.startswith("pdf:") else source_id
    parts = body.rsplit(":", 2)
    return unquote(parts[0]) if len(parts) == 3 else body


def _company_key(document_id: str) -> str:
    return re.sub(r"[^a-z0-9]", "", document_id.split("_")[0].lower())


# Questions often refer to a filer by its market abbreviation instead of its full name.
_DOCUMENT_ALIASES: tuple[tuple[str, str], ...] = (
    ("americanexpress", "amex"),
    ("jpmorgan", "jpm"),
    ("johnsonjohnson", "jnj"),
    ("proctergamble", "pg"),
    ("internationalbusinessmachines", "ibm"),
    ("generalmotors", "gm"),
    ("generalelectric", "ge"),
    ("merck", "mrk"),
    ("pfizer", "pfe"),
    ("walmart", "wmt"),
    ("microsoft", "msft"),
)


class _PageIndex:
    """Document-aware BM25 over every stored page, reranked with filing signals."""

    def __init__(self, entries: tuple[MemoryEntryRecord, ...]):
        self.citations: list[MemoryCitation] = []
        self.page_keys: list[str] = []
        self.heads: list[list[str]] = []
        self.statements: list[bool] = []
        self.statement_kinds: list[frozenset[str]] = []
        self.counts: list[dict[str, int]] = []
        self.lengths: list[int] = []
        self.tokens: list[list[str]] = []
        self.by_document: dict[str, list[int]] = {}
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
            head = words[:_HEADING_WIDTH]
            document = _company_key(_document_of(page_ref.source_id)) if page_ref is not None else ""
            position = len(self.citations)
            self.citations.append(record.citation)
            self.page_keys.append(page_ref.source_id if page_ref is not None else record.entry.entry_id)
            self.heads.append(head)
            self.statements.append(_is_statement(head))
            self.statement_kinds.append(_statement_kinds(head))
            self.counts.append(counts)
            self.lengths.append(len(words))
            self.tokens.append(words)
            if document:
                self.by_document.setdefault(document, []).append(position)

    def _target_document(self, question: str) -> str | None:
        lowered = question.lower()
        compact = re.sub(r"[^a-z0-9]", "", lowered)
        words = set(_TOKEN_PATTERN.findall(lowered))
        matches: list[str] = []
        for name in self.by_document:
            if len(name) >= 4 and name in compact:
                matches.append(name)
                continue
            if len(name) >= 2 and (
                name in words
                or re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", lowered)
            ):
                matches.append(name)
                continue
            if any(
                name.startswith(canonical)
                and len(alias) >= 3
                and re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered)
                for canonical, alias in _DOCUMENT_ALIASES
            ):
                matches.append(name)
        if not matches:
            return None
        return max(matches, key=len)

    def _expansions(
        self, terms: list[str]
    ) -> tuple[set[str], tuple[str, ...], list[tuple[str, ...]]]:
        pairs: set[str] = set()
        singles: set[str] = set()
        long_patterns: list[tuple[str, ...]] = []
        present = set(terms)
        for triggers, patterns in _EXPANSIONS:
            if not (triggers & present):
                continue
            for pattern in patterns:
                if len(pattern) == 1:
                    singles.add(pattern[0])
                elif len(pattern) == 2:
                    pairs.add(pattern[0] + " " + pattern[1])
                elif len(pattern) > 2:
                    long_patterns.append(pattern)
        return pairs, singles, long_patterns

    def _passage(self, position: int, idf: dict[str, float]) -> float:
        tokens = self.tokens[position]
        width = _PASSAGE_WIDTH
        if len(tokens) <= width:
            window = tokens
            counts: dict[str, int] = {}
            for token in window:
                if token in idf:
                    counts[token] = counts.get(token, 0) + 1
            if not counts:
                return 0.0
            norm = _BM25_K1 * (1.0 - _BM25_B + _BM25_B * len(window) / width)
            return sum(
                idf[term] * freq * (_BM25_K1 + 1.0) / (freq + norm)
                for term, freq in counts.items()
            )
        stride = width // 2
        norm = _BM25_K1
        best = 0.0
        for start in range(0, len(tokens) - width + 1, stride):
            counts = {}
            for token in tokens[start : start + width]:
                if token in idf:
                    counts[token] = counts.get(token, 0) + 1
            if not counts:
                continue
            score = sum(
                idf[term] * freq * (_BM25_K1 + 1.0) / (freq + norm)
                for term, freq in counts.items()
            )
            if score > best:
                best = score
        return best

    def rank(self, question: str, limit: int) -> tuple[MemoryCitation, ...]:
        terms = _query_terms(question)
        if not terms or not self.citations:
            return ()
        wanted = frozenset(
            kind for kind, triggers in _STATEMENT_INTENTS if triggers.intersection(terms)
        )
        target = self._target_document(question)
        candidates = self.by_document.get(target) if target else None
        if not candidates:
            candidates = list(range(len(self.citations)))
        total = len(candidates)
        floor = max(2, int(total * _COMMON_TERM_RATIO))
        term_df = {term: 0 for term in terms}
        for position in candidates:
            counts = self.counts[position]
            for term in terms:
                if term in counts:
                    term_df[term] += 1
        kept = [term for term in terms if term_df[term] <= floor]
        if kept:
            terms = kept
            term_df = {term: term_df[term] for term in terms}
        idf = {
            term: math.log(1.0 + (total - term_df[term] + 0.5) / (term_df[term] + 0.5))
            for term in terms
        }
        weight_total = sum(idf.values()) or 1.0
        average_length = sum(self.lengths[position] for position in candidates) / total
        pairs, singles, long_patterns = self._expansions(terms)
        lexical: list[tuple[float, int]] = []
        for position in candidates:
            counts = self.counts[position]
            length = self.lengths[position] or 1
            norm = _BM25_K1 * (1.0 - _BM25_B + _BM25_B * length / average_length)
            score = 0.0
            for term in terms:
                frequency = counts.get(term, 0)
                if frequency:
                    score += idf[term] * frequency * (_BM25_K1 + 1.0) / (frequency + norm)
            lexical.append((score, position))
        scale = max(score for score, _ in lexical) or 1.0
        passages = {
            position: self._passage(position, idf)
            for score, position in lexical
            if score > 0.0
        }
        passage_scale = max(passages.values(), default=0.0) or 1.0
        scored_pages: list[tuple[float, int]] = []
        for score, position in lexical:
            rank = score / scale
            if position in passages:
                rank += _PASSAGE_WEIGHT * passages[position] / passage_scale
            head = self.heads[position]
            rank += _HEADING_WEIGHT * sum(idf[term] for term in terms if term in head) / weight_total
            if pairs or singles or long_patterns:
                page_tokens = self.tokens[position]
                found: set[str] = set()
                for index in range(len(page_tokens) - 1):
                    key = page_tokens[index] + " " + page_tokens[index + 1]
                    if key in pairs:
                        found.add(key)
                if singles:
                    found.update(singles.intersection(page_tokens))
                for pattern in long_patterns:
                    span = len(pattern)
                    if any(
                        tuple(page_tokens[start : start + span]) == pattern
                        for start in range(len(page_tokens) - span + 1)
                    ):
                        found.add(" ".join(pattern))
                rank += _EXPANSION_WEIGHT * len(found)
            if self.statements[position]:
                rank += _STATEMENT_WEIGHT
            if self.statement_kinds[position] & wanted:
                rank += _INTENT_WEIGHT
            scored_pages.append((rank, position))
        scored = sorted(scored_pages, key=lambda item: -item[0])
        selected: list[MemoryCitation] = []
        seen: set[str] = set()
        for _, position in scored:
            key = self.page_keys[position]
            if key in seen:
                continue
            seen.add(key)
            selected.append(self.citations[position])
            if len(selected) >= limit:
                break
        return tuple(selected)


_INDEX_CACHE: dict[tuple[Any, ...], _PageIndex] = {}


def _index_key(memory_ref: Any, entries: tuple[MemoryEntryRecord, ...]) -> tuple[Any, ...]:
    digest = hashlib.blake2b(digest_size=16)
    for record in entries:
        digest.update(record.entry.entry_id.encode("utf-8", "replace"))
        digest.update(b"\x00")
        digest.update(str(len(record.entry.text)).encode("ascii"))
        digest.update(b"\x01")
    return (memory_ref.artifact_id, memory_ref.revision, len(entries), digest.hexdigest())


async def _page_index(memory) -> _PageIndex | None:
    page = await memory.list()
    memory_ref = page.memory_ref
    if memory_ref is None:
        return None
    key = _index_key(memory_ref, page.entries)
    index = _INDEX_CACHE.get(key)
    if index is None:
        index = _PageIndex(page.entries)
        if len(_INDEX_CACHE) >= 4:
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
