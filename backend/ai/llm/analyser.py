"""Standalone LLM orchestration, independent of rules, NLP, API and database."""
import hashlib
import json
import re

from pydantic import ValidationError

from backend.ai.framework.categories import Category, Level, load_framework
from backend.ai.framework.prompts import PROMPT_VERSION, build_system_prompt
from backend.ai.llm.client import OllamaClient, LLMError, Settings
from backend.ai.schemas.concern_schema import (
    AnalysisResult, Concern, Coverage, Evidence, Location, Metadata, ModelAnalysis, Summary,
)
from backend.ai.schemas.input_schema import Document

EVIDENCE_REPAIR_PROMPT = (
    "\nYour previous response failed source validation. Reanalyse the ORIGINAL document. "
    "Copy only exact contiguous substrings from the stated section, preserving punctuation "
    "and case. Use occurrence=1 for unique quotes. Visual Representation requires a section "
    "whose type is image_description. For this regeneration, select whole original "
    "sections as evidence using the constrained schema. Explain the specific concern "
    "within that section. Do not invent or paraphrase evidence.")


def validate_findings(raw: ModelAnalysis | dict | str, document: Document) -> list[Concern]:
    try:
        if isinstance(raw, str):
            parsed = ModelAnalysis.model_validate_json(raw)
        else:
            parsed = ModelAnalysis.model_validate(raw)
    except (ValidationError, ValueError):
        raise LLMError("invalid_response", "The LLM response did not match the CARE schema.") from None
    sections = {s.section_id: s for s in document.sections}
    concerns = []
    seen = set()
    for candidate in parsed.concerns:
        evidence = []
        for reference in candidate.evidence:
            section = sections.get(reference.section_id)
            if section is None:
                raise LLMError("invalid_evidence", "A finding refers to an unknown section.")
            matches = list(re.finditer(re.escape(reference.quote), section.text))
            if not matches:
                raise LLMError("invalid_evidence", "A finding's quoted evidence does not match its source.")
            # A unique exact quote has only one possible location. Small models
            # sometimes number evidence items instead of repeated quote matches.
            if len(matches) == 1:
                match = matches[0]
            elif reference.occurrence <= len(matches):
                match = matches[reference.occurrence - 1]
            else:
                raise LLMError("invalid_evidence", "A repeated quote has an invalid occurrence number; its location is ambiguous.")
            item = Evidence(
                location=Location(section_id=section.section_id, page=section.page,
                                  slide=section.slide, paragraph=section.paragraph,
                                  start_char=match.start(), end_char=match.end()),
                original_content=reference.quote, type=section.type,
            )
            if item not in evidence:
                evidence.append(item)
        if candidate.category == Category.VISUAL_REPRESENTATION and not any(
            e.type == "image_description" for e in evidence
        ):
            raise LLMError("unsupported_visual_evidence", "Visual findings require an explicitly supplied image description.")
        positions = sorted((e.location.section_id, e.location.start_char, e.location.end_char) for e in evidence)
        identity = json.dumps([document.document_id, candidate.category.value, positions])
        if identity in seen:
            continue
        seen.add(identity)
        concerns.append(Concern(
            concern_id="llm-" + hashlib.sha256(identity.encode()).hexdigest()[:20],
            category=candidate.category, severity=candidate.severity, confidence=candidate.confidence,
            explanation=candidate.explanation, recommendation=candidate.recommendation,
            location=evidence[0].location, original_content=evidence[0].original_content,
            evidence=evidence,
        ))
    order = {s.section_id: i for i, s in enumerate(document.sections)}
    return sorted(concerns, key=lambda c: (order[c.location.section_id], c.location.start_char, c.category.value))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class LLMAnalyser:
    def __init__(self, client: OllamaClient, max_input_chars: int = 4000):
        if max_input_chars <= 0:
            raise ValueError("max_input_chars must be positive")
        self.client = client
        self.max_input_chars = max_input_chars
        self.framework = load_framework()

    @classmethod
    def from_env(cls):
        settings = Settings.from_env()
        return cls(OllamaClient(settings), settings.max_input_chars)

    def analyse_document(self, document: Document | dict, on_progress=None) -> AnalysisResult:
        document = Document.model_validate(document)
        payload = document.model_dump_json()
        if len(payload) > self.max_input_chars:
            raise LLMError("input_too_large", "Document exceeds CARE_LLM_MAX_INPUT_CHARS. No text was truncated or analysed. Supply a smaller explicitly scoped document.")
        # A description-only document supplies visual evidence, not independent
        # textual examples. Route it to the visual rubric to avoid duplicating
        # the depicted roles as textual categories. Mixed documents keep all five.
        scope = "Visual Representation" if all(s.type == "image_description" for s in document.sections) else None
        prompt = build_system_prompt(self.framework, scope)
        if on_progress:
            on_progress(scope or "all five CARE categories")
        completion = self.client.complete(prompt, payload, scope)
        passes = ["visual_descriptions" if scope else "combined"]
        usage = dict(completion.usage)
        try:
            concerns = validate_findings(completion.analysis, document)
        except LLMError as error:
            if error.code not in {"invalid_evidence", "unsupported_visual_evidence"}:
                raise
            # One bounded regeneration, with the unchanged source. Never fuzzy
            # match, invent offsets, or silently discard invalid findings.
            prompt += EVIDENCE_REPAIR_PROMPT
            if on_progress:
                on_progress("source evidence again")
            completion = self.client.complete(prompt, payload, scope, grounded_sections=True)
            passes.append("evidence_repair")
            for key, value in completion.usage.items():
                usage[key] = usage.get(key, 0) + value
            concerns = validate_findings(completion.analysis, document)
        descriptions = any(s.type == "image_description" for s in document.sections)
        return AnalysisResult(
            document_id=document.document_id,
            summary=Summary(
                total_concerns=len(concerns),
                severity_counts={l.value: sum(c.severity == l for c in concerns) for l in Level},
                category_counts={k.value: sum(c.category == k for c in concerns) for k in Category},
            ), concerns=concerns,
            coverage=Coverage(
                analysed_section_ids=[s.section_id for s in document.sections],
                visual_analysis="descriptions_only" if descriptions else "not_performed",
                limitations=[
                    "Assessment covers only the supplied text, not omitted document content.",
                    "Actual images were not inspected; image descriptions are unverified supplied accounts.",
                    "Severity and confidence are qualitative model judgments requiring educator review.",
                ] + (["Evidence repair uses whole source sections; highlights may include surrounding context."]
                     if "evidence_repair" in passes else []),
            ),
            metadata=Metadata(
                provider=self.client.provider, model=completion.model,
                response_id=completion.response_id, usage=usage, analysis_passes=passes,
                framework_version=self.framework["version"],
                framework_sha256=digest(json.dumps(self.framework, sort_keys=True, ensure_ascii=False)),
                prompt_version=PROMPT_VERSION, prompt_sha256=digest(prompt),
                input_sha256=digest(payload),
            ),
        )


def analyse_document(document: Document | dict) -> dict:
    """Future backend entry point; raises LLMError on failure, never returns fake success."""
    return LLMAnalyser.from_env().analyse_document(document).model_dump(mode="json")
