"""Budgeted text windows with stable source identities and reserved overlap."""
import json

from backend.ai.framework.categories import load_framework
from backend.ai.framework.prompts import build_system_prompt
from backend.ai.llm.analyser import EVIDENCE_REPAIR_PROMPT
from backend.ai.llm.client import Settings
from backend.ai.schemas.concern_schema import ModelAnalysis
from backend.ai.schemas.input_schema import Document
from backend.ai.preprocessing.cleaner import prepare_text
from backend.ai.preprocessing.segmenter import boundary
from backend.extraction.common import ExtractionError, PREVIEW_PAGE_CHARS


def payload_budget(settings):
    schema = json.dumps(ModelAnalysis.model_json_schema(), separators=(",", ":"))
    system = build_system_prompt(load_framework()) + EVIDENCE_REPAIR_PROMPT + "\nReturn only JSON matching this schema:\n" + schema
    return settings.context_tokens - settings.max_output_tokens - 512 - len(system.encode("utf-8"))


def build_context(extraction, settings=None):
    """Return canonical segmented text, provenance and bounded AI documents.

    Source offsets refer to the extracted preview, not bytes in a ZIP/PDF file.
    Each chunk sends exactly the same text to all three independent layers.
    """
    settings = settings or Settings.from_env()
    original = prepare_text(extraction.document)
    byte_limit = payload_budget(settings)
    def document(sections):
        return Document(document_id=original.document_id, filename="extracted-text", sections=sections)
    def fits(sections):
        payload = document(sections).model_dump_json()
        return len(payload) <= settings.max_input_chars and len(payload.encode("utf-8")) <= byte_limit
    def fits_with_neighbour(section):
        # Reserve half the serialized budget for surrounding content, including
        # section metadata. Filling a window with one passage prevented overlap.
        other = section.model_copy(update={"section_id": section.section_id + "x"})
        return fits([section, other])
    sections, sources = [], {}
    for source in original.sections:
        start, part = 0, 1
        while start < len(source.text):
            remaining = source.text[start:]
            if not remaining.strip():
                # Whitespace alone carries no assessable wording. Its excluded
                # range is reported separately in the source coverage report.
                break
            identifier = f"{source.section_id}.{part}"
            candidate = source.model_copy(update={"section_id": identifier})
            lo, hi = 0, len(remaining)
            while lo < hi:
                middle = (lo + hi + 1) // 2
                if fits_with_neighbour(candidate.model_copy(update={"text": remaining[:middle]})):
                    lo = middle
                else:
                    hi = middle - 1
            if lo < 1 or not remaining[:lo].strip():
                raise ExtractionError("context_budget", "Local model context cannot fit this text with the CARE framework. Increase CARE_LLM_CONTEXT_TOKENS or the input character budget.")
            end = start + boundary(remaining, lo)
            item = candidate.model_copy(update={"text": source.text[start:end]})
            sections.append(item)
            sources[identifier] = {**extraction.sources[source.section_id], "original_section_id": source.section_id,
                                   "source_start_char": start, "source_end_char": end,
                                   "continues_before": start > 0, "continues_after": end < len(source.text)}
            if "preview_start_char" in sources[identifier]:
                global_start = sources[identifier]["preview_start_char"] + start
                sources[identifier].update(preview_start_char=global_start,
                    preview_page_start=global_start // PREVIEW_PAGE_CHARS + 1,
                    preview_page_end=(global_start + end - start - 1) // PREVIEW_PAGE_CHARS + 1)
            start, part = end, part + 1
    if len(sections) > 20000:
        raise ExtractionError("segment_limit", "Context building exceeds 20,000 segments. Split this document into smaller files.")
    canonical = document(sections)
    chunks, batch = [], []
    for section in sections:
        if batch and not fits(batch + [section]):
            chunks.append(document(batch))
            overlap = [batch[-1]]
            if not fits(overlap + [section]):
                raise ExtractionError("context_budget", "Cannot preserve neighbouring context within the configured model budget.")
            batch = overlap
        batch.append(section)
    if batch:
        chunks.append(document(batch))
    return canonical, sources, chunks
