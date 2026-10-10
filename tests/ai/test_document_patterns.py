"""Stage 6 tests use real local NLP with deterministic aggregation, no LLM calls."""
import pytest

from backend.ai.analysis.document_analyser import analyse_document_patterns
from backend.ai.preprocessing.context_builder import build_context
from backend.ai.preprocessing.entity_analyser import NLPAnalyser
from backend.ai.llm.client import Settings
from backend.ai.schemas.pipeline_schema import LayerResult
from backend.services.file_service import extract_text
from backend.services.analysis_service import analyse_extraction
from tests.backend.test_text_pipeline import engine


def analyse(text, modify=None):
    extraction = extract_text(text)
    document, sources, chunks = build_context(extraction, Settings())
    nlp = NLPAnalyser().analyse_document(document)
    if modify:
        modify(nlp, extraction)
    return analyse_document_patterns(document, sources, extraction, nlp)


def examples(sentences):
    return "\n\n".join(f"Example {i}: {s}" for i, s in enumerate(sentences, 1))


def test_single_example_never_establishes_document_pattern():
    report = analyse("A man is an engineer.")
    assert report.assessment == "isolated_example"
    assert report.findings == []


def test_multiple_explicit_examples_establish_repeated_roles_and_representation():
    report = analyse(examples(["A man is a software engineer.", "A male student is a developer.", "The boy is a programmer."]))
    assert report.status == "completed"
    assert report.assessment == "repeated_pattern"
    assert {f.category for f in report.findings} == {"Representation", "Professional Roles"}
    assert report.statistics["explicit_gender_example_counts"] == {"men": 3}
    assert len(report.findings[0].evidence) == 3
    assert all(f.review_status == "needs_contextual_review" for f in report.findings)


def test_balanced_examples_have_no_threshold_pattern():
    report = analyse(examples(["A man is an engineer.", "A woman is a developer.", "A nonbinary student is a programmer."]))
    assert report.assessment == "multiple_examples_no_threshold_pattern"
    assert not report.findings
    assert report.statistics["explicit_gender_example_counts"] == {"men": 1, "women": 1, "nonbinary": 1}


def test_technical_leadership_supporting_division_across_examples():
    report = analyse(examples([
        "A male student is a developer. A woman is an assistant.",
        "The man is an engineer. The female student is a secretary.",
        "A boy is a programmer. A girl is a receptionist.",
        "The male employee is a manager.", "A man is a director.", "A male student is a leader.",
    ]))
    roles = next(f for f in report.findings if f.category == "Professional Roles")
    assert {p["role"] for p in roles.statistics["patterns"]} == {"technical", "leadership", "supporting"}


def test_actions_require_gender_linked_verb_and_its_own_object():
    report = analyse(examples([
        "Men lead the software teams while women take notes.",
        "A man manages the project while a woman writes minutes.",
        "The male employee supervises the department while the female employee organises meetings.",
    ]))
    roles = next(f for f in report.findings if f.category == "Professional Roles")
    assert {p["role"] for p in roles.statistics["patterns"]} == {"leadership", "supporting"}
    other = analyse("A woman writes notes while a man develops software.")
    assert any(o.reference == "women" and o.role == "supporting" for o in other.observations)
    assert not any(o.reference == "women" and o.role == "technical" for o in other.observations)


def test_names_and_neutral_pronouns_do_not_establish_gender():
    report = analyse(examples(["John is an engineer.", "Mary is a programmer.", "Alex is an assistant.", "They lead the team.", "Professor Man is an engineer."]))
    assert report.statistics["explicit_gender_example_counts"] == {}
    assert not report.observations and not report.findings


def test_pronouns_are_separate_not_assumed_gender_identity():
    report = analyse(examples(["He is an engineer.", "He is a programmer.", "He is a developer."]))
    assert report.statistics["explicit_gender_example_counts"] == {}
    assert report.statistics["pronoun_only_example_counts"] == {"he/him": 3}
    assert not report.findings


@pytest.mark.parametrize("text", [
    "A man is not an engineer.",
    "This stereotype is false: men are programmers.",
    "Historically, men were engineers.",
    "Research reports that men are engineers.",
    "Every engineer should check his work.",
])
def test_contextual_discussion_negation_and_generic_text_are_not_patterns(text):
    report = analyse(examples([text, text, text]))
    assert not report.findings


def test_identical_repeated_sentences_and_overlap_do_not_inflate_counts():
    def duplicate(layer, extraction):
        layer.relationships *= 3
    report = analyse(examples(["A man is an engineer."] * 5), duplicate)
    assert report.statistics["explicit_gender_example_counts"] == {"men": 1}
    assert not report.findings


def test_one_label_spanning_paragraphs_remains_one_example():
    report = analyse("Example 1: A man is an engineer.\n\nA male student is a developer.\n\nThe boy is a programmer.")
    assert report.assessment == "isolated_example"
    assert not report.findings


def test_nonbinary_spelling_variants_are_supported():
    report = analyse("A non-binary student is a developer.")
    assert report.statistics["explicit_gender_example_counts"] == {"nonbinary": 1}


def test_compound_role_head_does_not_create_false_support_or_technical_roles():
    report = analyse("A woman is an assistant manager. A man is an engineering manager.")
    assert report.statistics["role_example_counts"]["supporting"] == {}
    assert report.statistics["role_example_counts"]["technical"] == {}
    assert report.statistics["role_example_counts"]["leadership"] == {"women": 1, "men": 1}


def test_partial_nlp_coverage_is_reported_on_patterns():
    def skip(layer, extraction):
        last = layer.analysed_section_ids.pop()
        layer.skipped_section_ids.append(last)
        layer.relationships = [r for r in layer.relationships if r.evidence.location.section_id != last]
    report = analyse(examples(["A man is an engineer.", "A male student is a developer.", "The boy is a programmer.", "A woman is a manager."]), skip)
    assert report.status == "partial"
    assert report.findings and all("incomplete_document_coverage" in f.review_reasons for f in report.findings)


def test_document_pass_integrated_and_failure_isolated(monkeypatch):
    from backend.ai.analysis.care_engine import CAREEngine
    from tests.backend.test_text_pipeline import EmptyLLM
    from backend.ai.llm.analyser import LLMAnalyser
    from backend.ai.rules.rule_engine import RuleAnalyser
    analyser = CAREEngine({"rules": RuleAnalyser, "nlp": NLPAnalyser, "llm": lambda: LLMAnalyser(EmptyLLM())})
    extraction = extract_text(examples(["A man is an engineer.", "A male student is a developer.", "The boy is a programmer."]))
    result = analyse_extraction(extraction, engine=analyser, settings=Settings())
    assert result["analysis"]["document_analysis"]["assessment"] == "repeated_pattern"
    def fail(*args):
        raise ValueError("private text")
    monkeypatch.setattr("backend.services.analysis_service.analyse_document_patterns", fail)
    failed = analyse_extraction(extraction, engine=analyser, settings=Settings())
    assert failed["status"] == "partial"
    assert failed["analysis"]["document_analysis"]["status"] == "failed"
    assert "private text" not in str(failed)


def test_no_nlp_results_mean_unavailable_not_balanced():
    extraction = extract_text("Teaching content.")
    doc, sources, chunks = build_context(extraction, Settings())
    report = analyse_document_patterns(doc, sources, extraction, LayerResult(layer="nlp", status="failed", skipped_section_ids=[s.section_id for s in doc.sections]))
    assert report.status == "unavailable" and report.assessment == "unavailable"


def test_table_rows_on_different_slides_remain_distinct_examples():
    extraction = extract_text("A man is an engineer.\n\nA male student is a developer.\n\nA boy is a programmer.")
    for i, section in enumerate(extraction.document.sections, 1):
        extraction.sources[section.section_id].update(slide=i, shape_id=7, row=1, column=1, kind="table_cell")
    doc, sources, chunks = build_context(extraction, Settings())
    report = analyse_document_patterns(doc, sources, extraction, NLPAnalyser().analyse_document(doc))
    assert report.statistics["distinct_explicit_gender_examples"] == 3
    assert report.assessment == "repeated_pattern"
