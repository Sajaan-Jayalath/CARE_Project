"""Stage 2 integration tests: contracts, provenance and failure isolation."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.ai.analysis.care_engine import CAREEngine
from backend.ai.llm.analyser import LLMAnalyser
from backend.ai.llm.client import Completion, LLMError
from backend.ai.preprocessing.entity_analyser import NLPAnalyser
from backend.ai.rules.rule_engine import RuleAnalyser
from backend.ai.schemas.concern_schema import ModelAnalysis
from backend.ai.schemas.input_schema import Document
from backend.ai.schemas.pipeline_schema import LayerResult


class LocalFixture:
    provider = "test-only"

    def complete(self, *args, **kwargs):
        return Completion(ModelAnalysis(concerns=[]), "fixture")


def factories(**overrides):
    return {"rules": RuleAnalyser, "nlp": NLPAnalyser,
            "llm": lambda: LLMAnalyser(LocalFixture()), **overrides}


def test_real_rules_and_nlp_share_source_locations_without_merging():
    doc = Document.model_validate({"document_id": "d", "sections": [
        {"section_id": "one", "text": "Every engineer should check his work.", "page": 2},
        {"section_id": "two", "text": "Women work as engineers.", "slide": 3},
    ]})
    original = doc.model_dump()
    result = CAREEngine(factories()).analyse_document(doc)
    assert result.status == "completed"
    assert doc.model_dump() == original
    assert result.layers["rules"].concerns[0].location.page == 2
    assert result.layers["rules"].concerns[0].confidence is None
    assert result.layers["nlp"].concerns == []
    assert any(r.relationship_type == "P1" and r.evidence.location.slide == 3 for r in result.layers["nlp"].relationships)
    assert result.layers["llm"].concerns == []  # Does not veto rule findings.
    assert result.reconciliation_status == "completed"
    assert result.findings[0].sources == ["Rules"]
    assert len(result.input_sha256) == 64


@pytest.mark.parametrize("failed", ["rules", "nlp", "llm"])
def test_initialisation_failure_is_isolated_and_sanitised(failed):
    def fail():
        raise RuntimeError("SECRET uploaded text and local path")
    result = CAREEngine(factories(**{failed: fail})).analyse_document(Document.from_text("Every nurse should check her notes."))
    assert result.status == "partial"
    assert result.layers[failed].status == "failed"
    assert sum(r.status == "completed" for r in result.layers.values()) == 2
    assert "SECRET" not in result.model_dump_json()


def test_all_failures_are_not_a_clean_document():
    def fail():
        raise LLMError("provider_timeout", "timeout")
    result = CAREEngine({k: fail for k in ("rules", "nlp", "llm")}).analyse_document(Document.from_text("A paragraph."))
    assert result.status == "failed"
    assert all(r.error.code == "provider_timeout" for r in result.layers.values())


def test_llm_input_limit_preserves_sibling_results():
    small = lambda: LLMAnalyser(LocalFixture(), max_input_chars=10)
    result = CAREEngine(factories(llm=small)).analyse_document(Document.from_text("Every engineer should check his work."))
    assert result.status == "partial"
    assert result.layers["llm"].error.code == "input_too_large"
    assert result.layers["rules"].concerns
    assert result.layers["nlp"].status == "completed"


def test_llm_result_from_another_revision_is_rejected():
    class Stale:
        def analyse_document(self, doc):
            return LLMAnalyser(LocalFixture()).analyse_document(Document.from_text("An earlier version."))
    result = CAREEngine(factories(llm=Stale)).analyse_document(Document.from_text("The current version."))
    assert result.layers["llm"].status == "failed"


def test_invalid_input_runs_no_layers():
    def fail():
        pytest.fail("Should not execute")
    with pytest.raises(ValidationError):
        CAREEngine({k: fail for k in ("rules", "nlp", "llm")}).analyse_document({"document_id": "x", "sections": []})


def test_a_mutating_layer_cannot_change_sibling_inputs():
    seen = []
    class Mutator:
        def analyse_document(self, doc):
            doc.sections[0].text = "changed"
            return LayerResult(layer="rules", analysed_section_ids=["s1"])
    class Observer:
        def analyse_document(self, doc):
            seen.append(doc.sections[0].text)
            return LayerResult(layer="nlp", analysed_section_ids=["s1"])
    CAREEngine(factories(rules=Mutator, nlp=Observer)).analyse_document(Document.from_text("Original content."))
    assert seen == ["Original content."]


def test_invalid_layer_evidence_is_rejected():
    class BadRules:
        def analyse_document(self, doc):
            result = RuleAnalyser().analyse_document(doc)
            result.concerns[0].evidence[0].original_content = "invented"
            return result
    result = CAREEngine(factories(rules=BadRules)).analyse_document(Document.from_text("Every engineer should check his work."))
    assert result.layers["rules"].status == "failed"
    assert result.layers["rules"].concerns == []
    assert result.status == "partial"


def test_duplicate_or_missing_coverage_rejected():
    bad = lambda: SimpleNamespace(analyse_document=lambda doc: LayerResult(layer="rules", analysed_section_ids=[]))
    result = CAREEngine(factories(rules=bad)).analyse_document(Document.from_text("Hello."))
    assert result.layers["rules"].status == "failed"


def test_descriptions_skip_wording_layers_and_mark_partial_scope():
    doc = Document.model_validate({"document_id": "v", "sections": [
        {"section_id": "s1", "text": "An illustration shows a chairman.", "type": "image_description"}]})
    result = CAREEngine(factories()).analyse_document(doc)
    assert result.status == "partial"
    assert result.layers["rules"].status == result.layers["nlp"].status == "skipped"
    assert result.layers["rules"].concerns == []
    assert result.layers["llm"].status == "completed"


def test_rule_ids_and_unicode_offsets_are_stable_and_document_specific():
    doc = Document.from_text("\U0001f4da Each programmer must check his code.")
    first = RuleAnalyser().analyse_document(doc).concerns[0]
    second = RuleAnalyser().analyse_document(doc).concerns[0]
    assert first == second
    assert doc.sections[0].text[first.location.start_char:first.location.end_char] == "his"
    other = deepcopy(doc)
    other.document_id = "different"
    assert RuleAnalyser().analyse_document(other).concerns[0].concern_id != first.concern_id
