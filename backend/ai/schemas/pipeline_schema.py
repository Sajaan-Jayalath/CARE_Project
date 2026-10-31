"""Independent contributions and reconciled findings with source provenance."""
import hashlib
import json
from typing import Any, Literal

from pydantic import Field

from backend.ai.schemas.concern_schema import Concern, Evidence, Location
from backend.ai.schemas.input_schema import StrictModel
from backend.ai.framework.categories import Category, Level
from backend.ai.schemas.document_schema import DocumentPatternReport


def evidence_for(section, start, end, quote):
    if not 0 <= start < end <= len(section.text) or section.text[start:end] != quote:
        raise ValueError("Evidence must match the unchanged source section.")
    return Evidence(original_content=quote, type=section.type, location=Location(
        section_id=section.section_id, page=section.page, slide=section.slide,
        paragraph=section.paragraph, start_char=start, end_char=end))


def source_id(prefix, document_id, section, details):
    value = json.dumps([document_id, section.section_id, section.text, details], ensure_ascii=False)
    return prefix + "-" + hashlib.sha256(value.encode()).hexdigest()[:20]


class Relationship(StrictModel):
    relationship_id: str
    source: Literal["NLP"] = "NLP"
    relationship_type: Literal["P1", "P2", "P3", "P4", "P5", "P6"]
    gender_term: str
    gender_reference: str
    related_term: str
    predicate: str
    dependency_path: str
    sentence_has_negation: bool
    action_object: str | None = None
    evidence: Evidence


class LayerError(StrictModel):
    code: str
    message: str


class LayerResult(StrictModel):
    layer: Literal["rules", "nlp", "llm"]
    status: Literal["completed", "failed", "skipped"] = "completed"
    concerns: list[Concern] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    analysed_section_ids: list[str] = Field(default_factory=list)
    skipped_section_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    elapsed_seconds: float = Field(default=0, ge=0)
    error: LayerError | None = None


class ReconciledFinding(StrictModel):
    finding_id: str
    category: Category
    sources: list[Literal["Rules", "LLM", "Vision"]]
    evidence: list[Evidence]
    contributions: list[Concern]
    supporting_relationships: list[Relationship] = Field(default_factory=list)
    relationship_support_basis: str = "Matched by compatible relationship type and overlapping source evidence; not proof of bias or endorsement."
    explanation: str
    recommendation: str
    severity: Level | None = None
    severity_basis: str
    confidence: Level | None = None
    confidence_basis: str
    review_status: Literal["potential_concern", "needs_contextual_review"]
    review_reasons: list[str] = Field(default_factory=list)
    # Image findings: what the vision model saw, where, and which visual criterion applies.
    scope: Literal["passage", "image", "document_pattern"] = "passage"
    criterion: str | None = None
    images: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    statistics: dict[str, Any] = Field(default_factory=dict)


class ReconciliationNotice(StrictModel):
    kind: Literal["possible_duplicate", "category_difference", "assessment_conflict", "unconfirmed_by_llm", "incomplete_analysis"]
    finding_ids: list[str]
    message: str


class ReconciledSummary(StrictModel):
    total_findings: int
    needs_review: int
    category_counts: dict[str, int]
    severity_counts: dict[str, int]
    confidence_counts: dict[str, int]


class PipelineResult(StrictModel):
    document_id: str
    input_sha256: str
    status: Literal["completed", "partial", "failed"]
    layers: dict[str, LayerResult]
    document_analysis: DocumentPatternReport | None = None
    reconciliation_status: Literal["not_performed", "completed", "partial", "failed"] = "not_performed"
    findings: list[ReconciledFinding] = Field(default_factory=list)
    notices: list[ReconciliationNotice] = Field(default_factory=list)
    summary: ReconciledSummary | None = None
    reconciliation_error: LayerError | None = None
    reconciliation_version: str | None = None
    limitations: list[str] = Field(default_factory=lambda: [
        "Ambiguous duplicates remain separate and marked for review; finding counts may include them.",
        "NLP relationships are supporting evidence, not bias findings.",
        "Only supplied text/OCR text/descriptions are processed; extraction and actual image inspection are not implemented here.",
    ])
