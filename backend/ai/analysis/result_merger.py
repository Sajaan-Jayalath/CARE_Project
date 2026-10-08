"""Deterministic reconciliation of validated layer outputs; no extra inference.

Containment alone is not enough: cross-layer merges require a unique one-to-one
candidate match. Rule hits wholly inside exactly one same-category LLM finding are
then folded into it, so one passage does not repeat as a finding per word.
"""
import hashlib
import json

from backend.ai.framework.categories import Category, Level
from backend.ai.schemas.pipeline_schema import ReconciledFinding, ReconciledSummary, ReconciliationNotice

VERSION = "care-reconciliation-2026-10-02.1"
RANK = {"Low": 0, "Medium": 1, "High": 2}
RELATION_TYPES = {
    "Gendered Language": set(),
    "Gender Stereotypes": {"P2", "P3", "P4", "P5"},
    "Professional Roles": {"P1"},
    "Representation": {"P6"},
    "Visual Representation": set(),
}


def span(e):
    return (e.location.section_id, e.location.start_char, e.location.end_char)


def overlaps(a, b):
    return a.location.section_id == b.location.section_id and max(a.location.start_char, b.location.start_char) < min(a.location.end_char, b.location.end_char)


def contains(a, b):
    return a.location.section_id == b.location.section_id and a.location.start_char <= b.location.start_char and b.location.end_char <= a.location.end_char


def same_issue(a, b):
    # Rules currently produce wording findings only. Different categories must
    # never be merged merely because they point at the same sentence.
    return (a.source != b.source and a.category == b.category
            and all(any(contains(x, y) or contains(y, x) for y in b.evidence) for x in a.evidence)
            and all(any(contains(x, y) or contains(y, x) for x in a.evidence) for y in b.evidence))


def assess(contributions):
    reasons = []
    severities = {c.severity for c in contributions if c.severity is not None}
    confidence = {c.confidence for c in contributions if c.confidence is not None}
    if severities:
        severity = max(severities, key=lambda v: RANK[v.value])
        severity_basis = "Highest supplied severity for review priority; original assessments are retained."
    else:
        severity = Level.LOW if all(c.source == "Rules" and c.category == Category.GENDERED_LANGUAGE for c in contributions) else None
        severity_basis = "Low wording-severity default; contextual impact has not been assessed." if severity else "Unassessed."
    if len(severities) > 1:
        reasons.append("severity_conflict")
    if len(confidence) > 1:
        reasons.append("confidence_conflict")
    certainty = min(confidence, key=lambda v: RANK[v.value]) if confidence else None
    if certainty == Level.LOW:
        reasons.append("low_confidence")
    return severity, severity_basis, certainty, "Lowest supplied confidence when assessments conflict; qualitative and uncalibrated. Agreement and NLP attachments do not raise it." if certainty else "Unassessed; deterministic matching does not establish contextual certainty.", reasons


def reconcile(document, result, merge_windows=False):
    """Return a new result, retaining all raw contributions and layer statuses."""
    output = result.model_copy(deep=True)
    output.reconciliation_version = VERSION
    if not any(r.status == "completed" for r in result.layers.values()):
        return output
    concerns = [c for name in ("rules", "llm") if result.layers[name].status == "completed" for c in result.layers[name].concerns]
    # Remove only identical assessments within a layer. Same spans with distinct
    # explanations/recommendations remain separate potential issues.
    groups = {}
    window_sets = {}
    for concern in concerns:
        data = concern.model_dump(mode="json", exclude={"concern_id"})
        if merge_windows:
            # Same source/category/exact evidence and suggested change across
            # windows represent a repeated assessment. Retain every differing
            # explanation/severity/confidence inside the resulting contribution
            # group. Different suggestions or broader/narrower evidence remain
            # ambiguous and must not bridge separate issues together.
            data = {"source": concern.source, "category": concern.category.value,
                    "evidence": sorted(span(e) for e in concern.evidence),
                    "recommendation": " ".join(concern.recommendation.split())}
        key = json.dumps(data, sort_keys=True)
        if merge_windows:
            layer = result.layers["rules" if concern.source == "Rules" else "llm"]
            assessment = hashlib.sha256(concern.model_dump_json(exclude={"concern_id"}).encode()).hexdigest()
            windows = set(layer.metadata.get("assessment_windows", {}).get(assessment, []))
            if not windows or windows & window_sets.get(key, set()):
                # Do not collapse distinct findings made within the same window.
                key = groups_key(concern)
            window_sets.setdefault(key, set()).update(windows)
        groups.setdefault(key, []).append(concern)
    groups = list(groups.values())
    by_section = {}
    for i, group in enumerate(groups):
        for evidence in group[0].evidence:
            by_section.setdefault(evidence.location.section_id, set()).add(i)
    candidates = {i: sorted(j for j in set().union(*(by_section[e.location.section_id] for e in group[0].evidence))
                           if i != j and same_issue(group[0], groups[j][0])) for i, group in enumerate(groups)}
    merged = []
    consumed = set()
    for i, group in enumerate(groups):
        if i in consumed:
            continue
        consumed.add(i)
        if len(candidates[i]) == 1:
            j = candidates[i][0]
            if candidates[j] == [i] and j not in consumed:
                group = group + groups[j]
                consumed.add(j)
        merged.append(group)
    merged = absorb_rule_hits(merged)

    order ={s.section_id: i for i, s in enumerate(document.sections)}
    incomplete = result.status != "completed"
    relationships = result.layers["nlp"].relationships if result.layers["nlp"].status == "completed" else []
    llm = result.layers["llm"]
    findings = []
    notices = []
    for group in merged:
        group = sorted(group, key=lambda c: (c.source, c.concern_id, c.explanation, c.recommendation))
        evidence = {span(e): e for c in group for e in c.evidence}
        evidence = sorted(evidence.values(), key=lambda e: (order[e.location.section_id], e.location.start_char, e.location.end_char))
        category = group[0].category
        sources = sorted({c.source for c in group})
        identity = json.dumps([result.input_sha256, category.value, sorted({groups_key(c) for c in group})], sort_keys=True)
        identifier = "care-" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        severity, severity_basis, confidence, confidence_basis, reasons = assess(group)
        if sources == ["Rules"]:
            reasons.append("context_not_assessed")
            if llm.status == "completed" and all(e.location.section_id in llm.analysed_section_ids for e in evidence):
                reasons.append("unconfirmed_by_llm")
                notices.append(ReconciliationNotice(kind="unconfirmed_by_llm", finding_ids=[identifier],
                    message="The LLM returned no uniquely matching finding. This is not an explicit rejection; retain the rule finding for contextual review."))
        if incomplete:
            reasons.append("incomplete_analysis")
        support = [r for r in relationships if r.relationship_type in RELATION_TYPES[category.value]
                   and any(overlaps(r.evidence, e) for e in evidence)]
        support = list({r.relationship_id: r for r in support}.values())
        support.sort(key=lambda r: r.relationship_id)
        if any(r.sentence_has_negation for r in support):
            reasons.append("negated_context")
        # Prefer the contextual explanation for display, but preserve every
        # contributor's original explanation and recommendation for review.
        primary = next((c for c in group if c.source == "LLM"), group[0])
        findings.append(ReconciledFinding(finding_id=identifier, category=category, sources=sources,
            evidence=evidence, contributions=group, supporting_relationships=support,
            explanation=primary.explanation, recommendation=primary.recommendation,
            severity=severity, severity_basis=severity_basis, confidence=confidence, confidence_basis=confidence_basis,
            review_status="needs_contextual_review" if reasons else "potential_concern", review_reasons=reasons))
        if "severity_conflict" in reasons or "confidence_conflict" in reasons:
            notices.append(ReconciliationNotice(kind="assessment_conflict", finding_ids=[identifier],
                message="Contributors supplied different severity/confidence assessments; originals are retained for review."))
    by_section = {}
    for i, finding in enumerate(findings):
        for evidence in finding.evidence:
            by_section.setdefault(evidence.location.section_id, set()).add(i)
    for i, a in enumerate(findings):
        neighbours = set().union(*(by_section[e.location.section_id] for e in a.evidence))
        for j in sorted(j for j in neighbours if j > i):
            b = findings[j]
            if not any(overlaps(x, y) for x in a.evidence for y in b.evidence):
                continue
            same_category = a.category == b.category
            reason = "possible_duplicate" if same_category else "category_difference"
            notices.append(ReconciliationNotice(kind=reason, finding_ids=[a.finding_id, b.finding_id],
                message="Overlapping findings cannot be safely identified as one issue; both are retained." if same_category else
                        "Different categories share evidence. Both may be valid; no category was removed or treated as an explicit contradiction."))
            for finding in (a, b):
                if reason not in finding.review_reasons:
                    finding.review_reasons.append(reason)
                finding.review_status = "needs_contextual_review"
    if incomplete:
        notices.append(ReconciliationNotice(kind="incomplete_analysis", finding_ids=[],
            message="At least one layer failed or skipped content. Findings cover completed contributions only, not a clean assessment of all content."))
    findings.sort(key=lambda f: (order[f.evidence[0].location.section_id], f.evidence[0].location.start_char, f.category.value, f.finding_id))
    output.findings = findings
    output.notices = notices
    output.summary = ReconciledSummary(total_findings=len(findings), needs_review=sum(f.review_status == "needs_contextual_review" for f in findings),
        category_counts={c.value: sum(f.category == c for f in findings) for c in Category},
        severity_counts={v: sum((f.severity.value if f.severity else "Unassessed") == v for f in findings) for v in ["Low", "Medium", "High", "Unassessed"]},
        confidence_counts={v: sum((f.confidence.value if f.confidence else "Unassessed") == v for f in findings) for v in ["Low", "Medium", "High", "Unassessed"]})
    output.reconciliation_status = "partial" if incomplete else "completed"
    return output


def absorb_rule_hits(merged):
    """Fold word-level rule hits into the one same-category LLM finding containing them.

    A passage-level LLM finding often covers several rule hits (e.g. 'he', 'his',
    'businessman'); listing each separately repeats one issue. A hit is absorbed
    only when exactly one LLM group of its category contains all of its evidence.
    Its evidence and original assessment remain as contributions.
    """
    hosts = [i for i, group in enumerate(merged) if any(c.source == "LLM" for c in group)]
    extra, absorbed = {}, set()
    for i, group in enumerate(merged):
        if any(c.source != "Rules" for c in group):
            continue
        hits = [e for c in group for e in c.evidence]
        matches = [j for j in hosts if merged[j][0].category == group[0].category
                   and all(any(contains(h, e) for c in merged[j] if c.source == "LLM" for h in c.evidence) for e in hits)]
        if len(matches) == 1:
            extra.setdefault(matches[0], []).extend(group)
            absorbed.add(i)
    return [group + extra.get(i, []) for i, group in enumerate(merged) if i not in absorbed]


def groups_key(concern):
    return json.dumps(concern.model_dump(mode="json", exclude={"concern_id"}), sort_keys=True)
