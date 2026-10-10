"""Stage 5: bounded surrounding context, coverage and repeated assessments."""
import pytest

from backend.ai.llm.client import Settings
from backend.ai.preprocessing.context_builder import build_context, payload_budget
from backend.ai.analysis.care_engine import CAREEngine
from backend.ai.rules.rule_engine import RuleAnalyser
from backend.ai.schemas.input_schema import Section
from backend.extraction.common import ExtractionError
from backend.services.file_service import extract_text
from backend.services.analysis_service import analyse_extraction
from tests.backend.test_text_pipeline import engine, EmptyNLP, EmptyLLM
from backend.ai.llm.analyser import LLMAnalyser
from backend.ai.framework.categories import Level


@pytest.mark.parametrize("text", [
    "Each student reviews the results. " * 600,
    "They review café examples — and discuss them 🙂. " * 300,
    "A" * 10000,
])
def test_long_passages_always_bridge_adjacent_windows(text):
    original = extract_text(text)
    doc, sources, chunks = build_context(original, Settings())
    assert len(chunks) > 2
    assert "".join(s.text for s in doc.sections) == text
    for previous, current in zip(chunks, chunks[1:]):
        assert previous.sections[-1] == current.sections[0]
    for chunk in chunks:
        assert len(chunk.model_dump_json().encode()) <= payload_budget(Settings())
        assert len(chunk.model_dump_json()) <= Settings().max_input_chars


def test_hundred_preview_pages_have_full_bounded_coverage():
    original = extract_text(("Students review their results. " * 10000)[:300000])
    assert original.page_count == 100
    doc, sources, chunks = build_context(original, Settings())
    assert sum(len(s.text) for s in doc.sections) == 300000
    assert {s.section_id for c in chunks for s in c.sections} == set(sources)
    assert max(len(c.model_dump_json().encode()) for c in chunks) <= payload_budget(Settings())


def test_tiny_context_rejected_before_any_inference():
    with pytest.raises(ExtractionError) as error:
        build_context(extract_text("Teaching text."), Settings(context_tokens=4096))
    assert error.value.code == "context_budget"


def test_cancelled_windows_are_visible_and_previous_results_survive():
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        return calls > 1
    result = analyse_extraction(extract_text("Every engineer should check his work. " * 80),
        engine=engine(), settings=Settings(), should_cancel=cancel)
    assert result["cancelled"]
    assert result["status"] == "partial"
    assert result["analysis"]["findings"]
    coverage = result["coverage"]["layers"]["llm"]
    assert coverage["sections"][0]["status"] == "partial"
    assert coverage["sections"][0]["processed_ranges"]
    assert coverage["sections"][0]["unprocessed_ranges"]
    assert result["chunks"][-1]["layers"]["llm"]["error"]["code"] == "cancelled"


def test_failed_window_does_not_erase_other_windows():
    real = engine()
    class FailOnce:
        calls = 0
        def analyse_document(self, doc):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("private path and content")
            return real.analyse_document(doc)
    result = analyse_extraction(extract_text("Every engineer should check his work. " * 80), engine=FailOnce(), settings=Settings())
    assert result["status"] == "partial"
    assert result["analysis"]["findings"]
    assert result["coverage"]["failed_or_unprocessed_chunks"] == [2]
    assert "private path" not in str(result)
    assert result["chunks"][1]["layers"]["rules"]["skipped_section_ids"]


def test_overlap_variants_merge_but_keep_assessments():
    class VaryingRules:
        calls = 0
        def analyse_document(self, doc):
            self.calls += 1
            result = RuleAnalyser().analyse_document(doc)
            for concern in result.concerns:
                concern.explanation = f"Contextual explanation from window {self.calls}."
                concern.severity = Level.LOW if self.calls % 2 else Level.HIGH
            return result
    rules = VaryingRules()
    analyser = CAREEngine({"rules": lambda: rules, "nlp": EmptyNLP, "llm": lambda: LLMAnalyser(EmptyLLM())})
    extraction = extract_text("placeholder")
    extraction.document.sections = [Section(section_id=f"s{i}", text="Every engineer should check his work.") for i in range(1, 12)]
    extraction.sources = {s.section_id: {} for s in extraction.document.sections}
    result = analyse_extraction(extraction, engine=analyser, settings=Settings(max_input_chars=550))
    assert len(result["analysis"]["findings"]) == 11
    assert any(len(f["contributions"]) > 1 for f in result["analysis"]["findings"])
    assert any("severity_conflict" in f["review_reasons"] for f in result["analysis"]["findings"])
    assert result["coverage"]["layers"]["rules"]["processed_characters"] == sum(len(s.text) for s in extraction.document.sections)


def test_extraction_warnings_survive_into_coverage():
    extraction = extract_text("Teaching text.")
    extraction.status = "partial"
    extraction.warnings.append({"code": "page_without_text", "page": 2, "message": "OCR required."})
    result = analyse_extraction(extraction, engine=engine(), settings=Settings())
    assert result["status"] == "partial"
    assert result["coverage"]["extraction_warnings"][0]["page"] == 2
    assert not result["coverage"]["complete_extraction"]


def test_separate_assessments_in_one_window_are_not_collapsed():
    class TwoIssues:
        def analyse_document(self, doc):
            result = RuleAnalyser().analyse_document(doc)
            first = result.concerns[0]
            second = first.model_copy(deep=True)
            second.concern_id += "-second"
            second.explanation = "Another potentially distinct interpretation."
            result.concerns.append(second)
            return result
    analyser = CAREEngine({"rules": TwoIssues, "nlp": EmptyNLP, "llm": lambda: LLMAnalyser(EmptyLLM())})
    result = analyse_extraction(extract_text("Every engineer should check his work."), engine=analyser, settings=Settings())
    assert len(result["analysis"]["findings"]) == 2
    assert all("possible_duplicate" in f["review_reasons"] for f in result["analysis"]["findings"])
