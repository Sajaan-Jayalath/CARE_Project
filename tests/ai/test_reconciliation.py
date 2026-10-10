"""Ambiguous overlap, provenance, conflicts and partial-result regressions."""
from copy import deepcopy
import hashlib

import pytest

from backend.ai.analysis.result_merger import reconcile
from backend.ai.schemas.concern_schema import Concern
from backend.ai.schemas.input_schema import Document
from backend.ai.schemas.pipeline_schema import LayerResult, PipelineResult, Relationship, evidence_for


def fixture(text="Every engineer should check his work."):
    doc = Document.from_text(text)
    result = PipelineResult(document_id=doc.document_id,
        input_sha256=hashlib.sha256(doc.model_dump_json().encode()).hexdigest(), status="completed",
        layers={name: LayerResult(layer=name, analysed_section_ids=["s1"]) for name in ("rules", "nlp", "llm")})
    return doc, result


def finding(doc, source, quote, category="Gendered Language", start=None, **kwargs):
    start = doc.sections[0].text.index(quote) if start is None else start
    e = evidence_for(doc.sections[0], start, start+len(quote), quote)
    return Concern(concern_id=f"{source}-{start}-{category}", source=source, category=category,
        evidence=[e], location=e.location, original_content=quote,
        explanation=kwargs.pop("explanation", "A potential concern."),
        recommendation=kwargs.pop("recommendation", "Consider an inclusive alternative."), **kwargs)


@pytest.mark.parametrize("quote", ["his", "Every engineer should check his work."])
def test_unique_exact_or_contained_cross_layer_match(quote):
    doc, result = fixture()
    result.layers["rules"].concerns = [finding(doc, "Rules", "his")]
    result.layers["llm"].concerns = [finding(doc, "LLM", quote, severity="Medium", confidence="Medium")]
    original = result.model_dump()
    output = reconcile(doc, result)
    assert result.model_dump() == original
    assert output.summary.total_findings == 1
    f = output.findings[0]
    assert f.sources == ["LLM", "Rules"]
    assert len(f.contributions) == 2
    assert f.confidence.value == "Medium"  # No agreement bonus.
    assert f.severity.value == "Medium"


def test_rule_hits_inside_one_llm_finding_are_grouped_into_it():
    doc, result = fixture("Every chairman should check his work.")
    result.layers["rules"].concerns = [finding(doc, "Rules", "chairman"), finding(doc, "Rules", "his")]
    result.layers["llm"].concerns = [finding(doc, "LLM", doc.sections[0].text, explanation="Contextual issue.")]
    output = reconcile(doc, result)
    assert len(output.findings) == 1
    f = output.findings[0]
    assert f.sources == ["LLM", "Rules"]
    assert f.explanation == "Contextual issue."
    assert len(f.contributions) == 3  # Rule assessments are retained, not deleted.
    assert {e.original_content for e in f.evidence} == {doc.sections[0].text, "chairman", "his"}
    assert not any(n.kind == "possible_duplicate" for n in output.notices)


def test_rule_hits_stay_separate_from_other_categories_and_ambiguous_hosts():
    doc, result = fixture("Every chairman should check his work.")
    text = doc.sections[0].text
    result.layers["rules"].concerns = [finding(doc, "Rules", "chairman")]
    # A different-category LLM finding never absorbs a wording hit.
    result.layers["llm"].concerns = [finding(doc, "LLM", text, category="Gender Stereotypes")]
    assert len(reconcile(doc, result).findings) == 2
    # Two same-category LLM findings containing the hit are ambiguous.
    result.layers["llm"].concerns = [finding(doc, "LLM", text, explanation="First."),
                                      finding(doc, "LLM", "Every chairman", explanation="Second.")]
    output = reconcile(doc, result)
    assert len(output.findings) == 3
    assert any(f.sources == ["Rules"] for f in output.findings)


def test_repeated_words_at_different_locations_stay_separate():
    doc, result = fixture("his work and his report")
    result.layers["rules"].concerns = [finding(doc, "Rules", "his", start=0), finding(doc, "Rules", "his", start=13)]
    output = reconcile(doc, result)
    assert len(output.findings) == 2
    assert len({f.finding_id for f in output.findings}) == 2


def test_identical_text_in_different_sections_is_not_a_duplicate():
    doc, result = fixture()
    other = doc.sections[0].model_copy(update={"section_id": "s2", "page": 2})
    doc.sections.append(other)
    first = finding(doc, "Rules", "his")
    second = finding(doc, "LLM", "his")
    second.evidence[0] = evidence_for(other, first.location.start_char, first.location.end_char, "his")
    second.location = second.evidence[0].location
    result.layers["rules"].concerns = [first]
    result.layers["llm"].concerns = [second]
    output = reconcile(doc, result)
    assert len(output.findings) == 2
    assert not any(n.kind == "possible_duplicate" for n in output.notices)


def test_partial_zero_findings_still_has_incomplete_notice():
    doc, result = fixture("Neutral teaching content.")
    result.status = "partial"
    result.layers["llm"].status = "failed"
    output = reconcile(doc, result)
    assert output.summary.total_findings == 0
    assert output.reconciliation_status == "partial"
    assert any(n.kind == "incomplete_analysis" for n in output.notices)


def test_distinct_same_span_assessments_are_not_silently_deleted():
    doc, result = fixture()
    result.layers["llm"].concerns = [finding(doc, "LLM", "his", explanation="First issue."),
                                      finding(doc, "LLM", "his", explanation="Different issue.")]
    output = reconcile(doc, result)
    assert len(output.findings) == 2
    assert output.notices[0].kind == "possible_duplicate"


def test_exact_duplicate_assessments_merge_without_losing_originals():
    doc, result = fixture()
    c = finding(doc, "LLM", "his")
    result.layers["llm"].concerns = [c]
    first = reconcile(doc, result)
    result.layers["llm"].concerns.append(deepcopy(c))
    output = reconcile(doc, result)
    assert len(output.findings) == 1
    assert len(output.findings[0].contributions) == 2
    assert first.findings[0].finding_id == output.findings[0].finding_id


def test_different_categories_share_evidence_without_merging():
    doc, result = fixture("Women should do administrative work because they are naturally more organised.")
    result.layers["llm"].concerns = [finding(doc, "LLM", doc.sections[0].text, category=c)
                                     for c in ["Gender Stereotypes", "Professional Roles"]]
    output = reconcile(doc, result)
    assert len(output.findings) == 2
    assert output.notices[0].kind == "category_difference"


def test_rule_only_is_retained_and_contextual_confidence_is_unassessed():
    doc, result = fixture()
    result.layers["rules"].concerns = [finding(doc, "Rules", "his")]
    f = reconcile(doc, result).findings[0]
    assert f.severity.value == "Low"
    assert f.confidence is None
    assert f.review_status == "needs_contextual_review"
    assert "unconfirmed_by_llm" in f.review_reasons


def test_conflicting_assessments_retain_values_and_choose_conservative_confidence():
    doc, result = fixture()
    result.layers["rules"].concerns = [finding(doc, "Rules", "his", severity="Low", confidence="Low")]
    result.layers["llm"].concerns = [finding(doc, "LLM", "his", severity="High", confidence="High")]
    output = reconcile(doc, result)
    f = output.findings[0]
    assert f.severity.value == "High" and f.confidence.value == "Low"
    assert {"severity_conflict", "confidence_conflict"} <= set(f.review_reasons)
    assert any(n.kind == "assessment_conflict" for n in output.notices)
    assert {c.severity.value for c in f.contributions} == {"Low", "High"}


def relationship(doc, quote, kind="P1", negated=False):
    start = doc.sections[0].text.index(quote)
    return Relationship(relationship_id=kind+str(start), relationship_type=kind,
        gender_term="women", gender_reference="women", related_term="engineers", predicate="work",
        dependency_path="linguistic path", sentence_has_negation=negated,
        evidence=evidence_for(doc.sections[0], start, start+len(quote), quote))


def test_nlp_attachment_requires_compatible_category_and_overlapping_location():
    doc, result = fixture("Women work as engineers. Men are patient.")
    result.layers["llm"].concerns = [finding(doc, "LLM", "Women work as engineers.", category="Professional Roles")]
    result.layers["nlp"].relationships = [relationship(doc, "Women work as engineers."),
        relationship(doc, "Men are patient."), relationship(doc, "Women work as engineers.", kind="P3")]
    f = reconcile(doc, result).findings[0]
    assert [r.relationship_id for r in f.supporting_relationships] == ["P10"]
    assert f.confidence is None


def test_nlp_only_does_not_generate_concerns():
    doc, result = fixture("Women work as engineers.")
    result.layers["nlp"].relationships = [relationship(doc, doc.sections[0].text)]
    assert reconcile(doc, result).summary.total_findings == 0


def test_negated_relationship_adds_context_review_not_confidence():
    doc, result = fixture("Women are not incompetent.")
    result.layers["llm"].concerns = [finding(doc, "LLM", doc.sections[0].text, category="Gender Stereotypes", confidence="Medium")]
    result.layers["nlp"].relationships = [relationship(doc, doc.sections[0].text, kind="P2", negated=True)]
    f = reconcile(doc, result).findings[0]
    assert "negated_context" in f.review_reasons
    assert f.confidence.value == "Medium"


@pytest.mark.parametrize("failed", ["rules", "nlp", "llm"])
def test_partial_output_does_not_use_failed_layer_contributions(failed):
    doc, result = fixture()
    for layer, source in [("rules", "Rules"), ("llm", "LLM")]:
        result.layers[layer].concerns = [finding(doc, source, "his")]
    result.layers[failed].status = "failed"
    result.status = "partial"
    output = reconcile(doc, result)
    assert output.status == output.reconciliation_status == "partial"
    assert all("incomplete_analysis" in f.review_reasons for f in output.findings)
    if failed == "llm":
        assert output.findings[0].sources == ["Rules"]
        assert "unconfirmed_by_llm" not in output.findings[0].review_reasons


def test_failed_analysis_is_not_a_zero_finding_success():
    doc, result = fixture()
    for layer in result.layers.values():
        layer.status = "failed"
    result.status = "failed"
    output = reconcile(doc, result)
    assert output.status == "failed" and output.summary is None
    assert output.reconciliation_status == "not_performed"


def test_stable_order_and_ids_when_input_order_changes():
    doc, result = fixture("The chairman needs manpower.")
    result.layers["rules"].concerns = [finding(doc, "Rules", "chairman"), finding(doc, "Rules", "manpower")]
    first = reconcile(doc, result)
    result.layers["rules"].concerns.reverse()
    second = reconcile(doc, result)
    assert [(f.finding_id, f.evidence) for f in first.findings] == [(f.finding_id, f.evidence) for f in second.findings]


def test_reconciliation_failure_preserves_layer_results(monkeypatch):
    from backend.ai.analysis.care_engine import CAREEngine
    from tests.ai.test_engine import factories
    def fail(*args):
        raise RuntimeError("sensitive details")
    monkeypatch.setattr("backend.ai.analysis.result_merger.reconcile", fail)
    output = CAREEngine(factories()).analyse_document(Document.from_text("Every engineer should check his work."))
    assert output.status == "partial" and output.reconciliation_status == "failed"
    assert output.layers["rules"].concerns
    assert output.summary is None
    assert "sensitive" not in output.model_dump_json()
