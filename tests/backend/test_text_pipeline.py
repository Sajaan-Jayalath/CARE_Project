from backend.ai.analysis.care_engine import CAREEngine
from backend.ai.llm.analyser import LLMAnalyser
from backend.ai.llm.client import Completion, Settings
from backend.ai.preprocessing.context_builder import build_context, payload_budget
from backend.ai.rules.rule_engine import RuleAnalyser
from backend.ai.schemas.concern_schema import ModelAnalysis
from backend.ai.schemas.pipeline_schema import LayerResult
from backend.services.file_service import extract_text
from backend.services.analysis_service import analyse_extraction
from backend.ai.schemas.input_schema import Section


class EmptyLLM:
    provider = "test-only"
    def complete(self, *args, **kwargs):
        return Completion(ModelAnalysis(concerns=[]), "fixture")


class EmptyNLP:
    def analyse_document(self, doc):
        return LayerResult(layer="nlp", analysed_section_ids=[s.section_id for s in doc.sections])


def engine(llm=None):
    return CAREEngine({"rules": RuleAnalyser, "nlp": EmptyNLP,
                       "llm": llm or (lambda: LLMAnalyser(EmptyLLM()))})


def test_long_unicode_text_is_preserved_and_every_chunk_fits():
    text = ("Every programmer should test his code. — Then review the results.\n\n" * 120)
    extracted = extract_text(text)
    settings = Settings()
    doc, sources, chunks = build_context(extracted, settings)
    assert len(chunks) > 1
    assert "".join(s.text for s in doc.sections) == text
    assert set(s.section_id for c in chunks for s in c.sections) == set(sources)
    for chunk in chunks:
        assert len(chunk.model_dump_json()) <= settings.max_input_chars
        assert len(chunk.model_dump_json().encode()) <= payload_budget(settings)
    for section in doc.sections:
        source = sources[section.section_id]
        start = source["input_start_char"] + source["source_start_char"]
        end = source["input_start_char"] + source["source_end_char"]
        assert text[start:end] == section.text


def test_pipeline_findings_map_back_to_unchanged_original():
    text = "Every programmer should test his code. " * 45
    result = analyse_extraction(extract_text(text), engine=engine(), settings=Settings())
    assert result["status"] == "completed"
    assert len(result["chunks"]) > 1
    findings = result["analysis"]["findings"]
    assert findings
    positions = []
    for finding in findings:
        for evidence in finding["evidence"]:
            loc = evidence["location"]
            source = result["analysis_sources"][loc["section_id"]]
            start = source["source_start_char"] + loc["start_char"]
            end = source["source_start_char"] + loc["end_char"]
            assert text[start:end] == evidence["original_content"]
            positions.append((start, end))
    assert len(positions) == len(set(positions))


def test_failed_chunk_keeps_successes_and_records_missing_coverage():
    class Sometimes:
        def analyse_document(self, doc):
            if doc.sections[0].section_id != "s1.1":
                raise RuntimeError("inference failed")
            return LLMAnalyser(EmptyLLM()).analyse_document(doc)
    result = analyse_extraction(extract_text("Every programmer should test his code. " * 65), engine=engine(Sometimes), settings=Settings())
    assert result["status"] == "partial"
    assert result["analysis"]["layers"]["llm"]["skipped_section_ids"]
    assert result["analysis"]["findings"]
    assert any(c["layers"]["llm"]["error"] for c in result["chunks"])


def test_no_text_does_not_run_layers():
    extraction = extract_text("placeholder")
    extraction.document = None
    extraction.status = "no_text"
    result = analyse_extraction(extraction, engine=object())
    assert result["status"] == "no_text"
    assert result["analysis"] is None


def test_overlapping_windows_do_not_duplicate_identical_findings():
    extraction = extract_text("placeholder")
    extraction.document.sections = [Section(section_id=f"s{i}", text="Every engineer should check his work.") for i in range(1, 10)]
    extraction.sources = {s.section_id: {"paragraph": i} for i, s in enumerate(extraction.document.sections, 1)}
    doc, sources, chunks = build_context(extraction, Settings(max_input_chars=550))
    assert sum(len(c.sections) for c in chunks) > len(doc.sections)
    result = analyse_extraction(extraction, engine=engine(), settings=Settings(max_input_chars=550))
    concerns = result["analysis"]["layers"]["rules"]["concerns"]
    assert len(concerns) == 9


def test_generated_preview_page_locations_follow_segment_offsets():
    extraction = extract_text("A long teaching passage. " * 400)
    doc, sources, chunks = build_context(extraction, Settings())
    assert max(s["preview_page_start"] for s in sources.values()) >= 3
    for section in doc.sections:
        source = sources[section.section_id]
        assert source["preview_page_start"] == source["source_start_char"] // 3000 + 1
