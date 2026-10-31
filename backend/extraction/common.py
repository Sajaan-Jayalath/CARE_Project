"""Bounded extraction and a separate source map, kept out of model prompts."""
import hashlib
from typing import Any, Literal

from pydantic import Field

from backend.ai.schemas.input_schema import Document, Section, StrictModel

MAX_BYTES = 100_000_000
MAX_PAGES = 100
MAX_CHARS = 1_000_000
PREVIEW_PAGE_CHARS = 3000
MAX_SECTIONS = 20000


class ExtractionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ExtractionResult(StrictModel):
    document_id: str
    filename: str
    format: str
    file_sha256: str
    status: Literal["completed", "partial", "no_text"]
    document: Document | None
    sources: dict[str, dict[str, Any]]
    page_count: int
    page_basis: str
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    image_analysis: str = "not_performed"


class Collector:
    def __init__(self, data, filename, format):
        self.digest = hashlib.sha256(data).hexdigest()
        self.document_id = "doc-" + self.digest[:20]
        self.filename, self.format = filename, format
        self.sections, self.sources, self.warnings = [], {}, []
        self.characters = 0
        self.page_count = 0
        self.page_basis = "generated_text_preview_3000_characters"

    def warn(self, code, message, **location):
        # Aggregate generic notices; retain distinct source locations.
        item = {"code": code, "message": message, **location}
        if item not in self.warnings:
            self.warnings.append(item)

    def add(self, text, **source):
        if not text or not text.strip():
            return
        if "\ufffd" in text or "\x00" in text:
            self.warn("unreadable_characters", "Some extracted characters are unreadable; review the original.", **source)
        if self.characters + len(text) > MAX_CHARS or len(self.sections) >= MAX_SECTIONS:
            raise ExtractionError("content_limit", "Document exceeds the extracted text or section limit.")
        section_id = f"s{len(self.sections) + 1}"
        if self.page_basis.startswith("generated"):
            source["preview_start_char"] = self.characters
            source["preview_page_start"] = self.characters // PREVIEW_PAGE_CHARS + 1
            source["preview_page_end"] = (self.characters + len(text) - 1) // PREVIEW_PAGE_CHARS + 1
            self.page_count = source["preview_page_end"]
        self.characters += len(text)
        if self.page_count > MAX_PAGES:
            raise ExtractionError("page_limit", "Document exceeds 100 native pages/slides or generated text preview pages.")
        self.sections.append(Section(section_id=section_id, text=text, page=source.get("page"),
                                     slide=source.get("slide"), paragraph=source.get("paragraph")))
        self.sources[section_id] = source

    def finish(self):
        return ExtractionResult(document_id=self.document_id, filename=self.filename, format=self.format,
            file_sha256=self.digest, status="no_text" if not self.sections else "partial" if self.warnings else "completed",
            document=Document(document_id=self.document_id, filename=self.filename, sections=self.sections) if self.sections else None,
            sources=self.sources, page_count=self.page_count, page_basis=self.page_basis, warnings=self.warnings)
