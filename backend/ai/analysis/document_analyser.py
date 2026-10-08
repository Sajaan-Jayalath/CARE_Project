"""Aggregate grounded NLP observations across windows; never infer gender from names.

Counts are appearances in distinct source units, NOT demographic counts of people.
Thresholds identify review candidates, not a research-validated test of bias.
"""
import hashlib
import json
import re
from collections import defaultdict

from backend.ai.schemas.document_schema import DocumentObservation, DocumentPattern, DocumentPatternReport
from backend.ai.schemas.pipeline_schema import evidence_for

GENDER_WORDS = {
    "man": "men", "men": "men", "male": "men", "boy": "men", "boys": "men", "father": "men",
    "woman": "women", "women": "women", "female": "women", "girl": "women", "girls": "women", "mother": "women",
    "nonbinary": "nonbinary", "non-binary": "nonbinary",
}
PRONOUNS = {"he": "he/him", "him": "he/him", "his": "he/him", "himself": "he/him",
            "she": "she/her", "her": "she/her", "hers": "she/her", "herself": "she/her"}
ROLE_WORDS = {
    "technical": r"\b(?:engineers?|engineering|programmers?|developers?|scientists?|technicians?|analysts?|architects?|coders?|testers?)\b",
    "leadership": r"\b(?:leaders?|managers?|directors?|chiefs?|supervisors?)\b",
    "supporting": r"\b(?:assistants?|secretar(?:y|ies)|receptionists?|administrators?|coordinators?|support|note[- ]takers?)\b",
}
EXAMPLE = re.compile(r"\b(?:example|scenario|case study)\s+(?:[A-Z]|\d+)(?:\b|:)", re.I)
CONTEXT = re.compile(r"\b(?:stereotypes?|sexism|bias(?:ed)?|discriminat\w*|historical(?:ly)?|survey|research|criticis\w*|criticiz\w*|reject\w*|myth|counterexample|untrue|false|incorrect)\b|\bstudy\s+(?:found|reports?|shows?)\b", re.I)
GENERIC = re.compile(r"\b(?:every|each|any)\b|\b(?:men|women|boys|girls)\s+(?:always|never|naturally)\b|\bshould\b", re.I)


def identity(prefix, value):
    return prefix + "-" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def action_role(relationship):
    """Require the gender-linked verb AND its own direct object, not nearby words."""
    verb = relationship.related_term.casefold()
    obj = (relationship.action_object or "").casefold()
    if re.search(r"\b(?:leads?|led|manag\w*|supervis\w*|direct\w*)\b", verb) and re.search(r"\b(?:teams?|projects?|departments?|groups?)\b", obj):
        return "leadership"
    if re.search(r"\b(?:writ\w*|wrote|design\w*|develop\w*|debug\w*|implement\w*|test\w*|program\w*)\b", verb) and re.search(r"\b(?:code|programs?|software|algorithms?|databases?|applications?|networks?)\b", obj):
        return "technical"
    if re.search(r"\b(?:tak\w*|took|writ\w*|wrote|organis\w*|organiz\w*|schedul\w*|arrang\w*|prepar\w*)\b", verb) and re.search(r"\b(?:notes|minutes|meetings?|schedules?|appointments?)\b", obj):
        return "supporting"
    return None


def nominal_role(phrase):
    # The final occupational noun is the head in ordinary English compounds:
    # "assistant manager" is leadership, not an administrative assistant.
    matches = [(m.start(), role) for role, pattern in ROLE_WORDS.items() for m in re.finditer(pattern, phrase, re.I)]
    return max(matches)[1] if matches else None


def example_units(extraction):
    """Explicit labels span subsequent paragraphs; otherwise source units are proxies."""
    result, active = {}, None
    for section in extraction.document.sections:
        matches = list(EXAMPLE.finditer(section.text))
        labels = [(m.start(), identity("example", [section.section_id, m.start()])) for m in matches]
        source = extraction.sources.get(section.section_id, {})
        if labels:
            result[section.section_id] = (active, labels, None)
            active = labels[-1][1]
        elif active:
            result[section.section_id] = (active, [], None)
        else:
            cell = str(source.get("cell", ""))
            row = source.get("row") or (re.search(r"\d+$", cell).group() if re.search(r"\d+$", cell) else None)
            # Cells in the same table/spreadsheet row are not independent examples.
            proxy = [source.get("part"), source.get("sheet"), source.get("page"), source.get("slide"),
                     source.get("shape_id"), source.get("group_path"), source.get("table"), row] if row is not None else [section.section_id]
            result[section.section_id] = (None, [], identity("source-unit", proxy))
    return result


def analyse_document_patterns(document, sources, extraction, nlp_layer, minimum_examples=3, dominance=0.8):
    if minimum_examples < 3 or not 0.5 < dominance <= 1:
        raise ValueError("Pattern thresholds must require at least three examples and a majority.")
    analysed = set(nlp_layer.analysed_section_ids)
    report = DocumentPatternReport(status="completed", assessment="insufficient_evidence",
        policy={"minimum_distinct_examples": minimum_examples, "dominance_threshold": dominance,
                "counting_unit": "one appearance per explicit reference group per example/source unit",
                "name_based_gender_inference": False, "pronouns_are_gender_identity": False},
        coverage={"analysed_segment_ids": sorted(analysed), "unprocessed_segment_ids": nlp_layer.skipped_section_ids,
                  "extraction_complete": extraction.status == "completed"},
        limitations=["Counts describe extracted appearances, not unique people, population proportions or proven bias.",
                    "Unlabelled paragraphs/blocks/rows are example proxies; they may belong to one larger example.",
                    "Names, neutral pronouns and unknown gender are not assigned a gender. Pronoun-only observations are separate.",
                    "Missing explicit references do not prove that a gender is absent. Only supported NLP relationships are counted.",
                    "Repeated identical sentences count once; repeated references to one person may still occur in different source units.",
                    "Context filters and role vocabulary are conservative heuristics; educator review is required."])
    if not analysed:
        report.status, report.assessment = "unavailable", "unavailable"
        return report
    if nlp_layer.skipped_section_ids or extraction.status != "completed":
        report.status = "partial"
    sections = {s.section_id: s for s in document.sections}
    original = {s.section_id: s for s in extraction.document.sections}
    units = example_units(extraction)
    unique_sentences, observations = {}, {}
    role_phrases = defaultdict(set)
    for r in nlp_layer.relationships:
        if r.relationship_type == "P1":
            role_phrases[(r.gender_term.casefold(), r.evidence.model_dump_json())].add(r.related_term.casefold())
    for relationship in nlp_layer.relationships:
        if relationship.relationship_type not in {"P1", "P6"}:
            continue
        if relationship.relationship_type == "P1":
            phrase = relationship.related_term.casefold()
            peers = role_phrases[(relationship.gender_term.casefold(), relationship.evidence.model_dump_json())]
            if any(phrase != other and re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", other) for other in peers):
                continue  # compound modifier already covered by its full role
        evidence = relationship.evidence
        loc = evidence.location
        section = sections[loc.section_id]
        if loc.section_id not in analysed or evidence_for(section, loc.start_char, loc.end_char, evidence.original_content) != evidence:
            raise ValueError("Ungrounded document observation")
        source = sources[loc.section_id]
        parent = original[source["original_section_id"]]
        word = relationship.gender_term.casefold()
        reference = GENDER_WORDS.get(word) or PRONOUNS.get(word)
        reason = None
        if reference is None:
            reason = "no_supported_explicit_reference"
        elif relationship.sentence_has_negation:
            reason = "negated_relationship"
        elif CONTEXT.search(parent.text):
            reason = "research_historical_or_critical_context"
        elif GENERIC.search(evidence.original_content):
            reason = "generic_or_prescriptive_statement_not_an_example"
        elif any(x in source.get("part", "") for x in ("header", "footer")) or source.get("kind") == "speaker_notes":
            reason = "supplementary_or_repeated_content"
        if reason:
            report.excluded_observations.append({"relationship_id": relationship.relationship_id, "reason": reason,
                                                 "evidence": evidence.model_dump(mode="json")})
            continue
        active, labels, proxy = units[parent.section_id]
        position = source["source_start_char"] + loc.start_char
        for offset, label in labels:
            if offset <= position:
                active = label
        # A sentence can start with its own Example label, before the gender word.
        if labels and not active:
            active = labels[0][1]
        unit = active or proxy or identity("source-unit", [parent.section_id])
        basis = "explicit_example_label" if active else "source_unit_proxy"
        fingerprint = " ".join(EXAMPLE.sub("", evidence.original_content).casefold().split())
        unit = unique_sentences.setdefault(fingerprint, unit)
        roles = [nominal_role(relationship.related_term)] if relationship.relationship_type == "P1" else []
        if relationship.relationship_type == "P6":
            roles = [action_role(relationship)]
        if not roles:
            roles = [None]
        for role in roles:
            # One canonical observation per source example, group and role.
            key = (unit, reference, role)
            if key in observations:
                if relationship.relationship_id not in observations[key].relationship_ids:
                    observations[key].relationship_ids.append(relationship.relationship_id)
                continue
            observations[key] = DocumentObservation(observation_id=identity("observation", [document.document_id, key]),
                example_id=unit, example_basis=basis, reference=reference,
                reference_basis="explicit_gender_word" if word in GENDER_WORDS else "pronoun_only",
                role=role, evidence=evidence, relationship_ids=[relationship.relationship_id])
    report.observations = list(observations.values())
    groups, pronouns = defaultdict(set), defaultdict(set)
    role_groups = {role: defaultdict(set) for role in ROLE_WORDS}
    for item in report.observations:
        if item.reference_basis == "pronoun_only":
            pronouns[item.reference].add(item.example_id)
            continue
        groups[item.reference].add(item.example_id)
        if item.role:
            role_groups[item.role][item.reference].add(item.example_id)
    counts = {k: len(v) for k, v in groups.items()}
    unit_ids = set().union(*groups.values()) if groups else set()
    report.statistics = {"explicit_gender_example_counts": counts,
        "pronoun_only_example_counts": {k: len(v) for k, v in pronouns.items()},
        "distinct_explicit_gender_examples": len(unit_ids),
        "role_example_counts": {role: {k: len(v) for k, v in values.items()} for role, values in role_groups.items()},
        "sections_without_supported_observations": sorted(set(original) - {sources[o.evidence.location.section_id]["original_section_id"] for o in report.observations})}
    if len(unit_ids) == 1:
        report.assessment = "isolated_example"
    elif len(unit_ids) >= minimum_examples:
        report.assessment = "multiple_examples_no_threshold_pattern"

    def flag(category, selected, stats, explanation, recommendation):
        evidence = {e.evidence.model_dump_json(): e.evidence for e in selected}
        reasons = ["pattern_requires_contextual_review", "heuristic_threshold_not_validated_accuracy"]
        if any(o.example_basis == "source_unit_proxy" for o in selected):
            reasons.append("example_boundaries_are_proxies")
        if report.status == "partial":
            reasons.append("incomplete_document_coverage")
        report.findings.append(DocumentPattern(finding_id=identity("document-pattern", [document.document_id, category, stats]),
            category=category, explanation=explanation, recommendation=recommendation,
            evidence=list(evidence.values()), observation_ids=[o.observation_id for o in selected], statistics=stats, review_reasons=reasons))

    total = sum(counts.values())
    if len(unit_ids) >= minimum_examples and total:
        dominant = max(counts, key=counts.get)
        if counts[dominant] >= minimum_examples and counts[dominant] / total >= dominance:
            flag("Representation", [o for o in report.observations if o.reference_basis == "explicit_gender_word"],
                {"counts": counts, "dominant_reference": dominant, "share_of_observed_group_appearances": counts[dominant] / total},
                f"Across the observed examples, {dominant} appear in {counts[dominant]} source examples and account for {counts[dominant]}/{total} explicit-gender group appearances. This is a repeated representation pattern to review, not proof of bias or absence of other genders.",
                "Review the purpose and boundaries of these examples. Where appropriate, vary explicitly represented genders across examples without altering factual identities.")
    patterns = []
    for role, values in role_groups.items():
        size = sum(len(v) for v in values.values())
        if not size:
            continue
        group = max(values, key=lambda k: len(values[k]))
        if len(values[group]) >= minimum_examples and len(values[group]) / size >= dominance:
            patterns.append({"role": role, "dominant_reference": group, "examples": len(values[group]),
                             "total_group_appearances": size, "counts": {k: len(v) for k, v in values.items()}})
    if patterns:
        roles = {p["role"] for p in patterns}
        flag("Professional Roles", [o for o in report.observations if o.role in roles and o.reference_basis == "explicit_gender_word"],
            {"patterns": patterns},
            "Repeated role associations occur across observed examples: " + "; ".join(f"{p['role']} roles: {p['dominant_reference']} in {p['examples']}/{p['total_group_appearances']} observed group appearances" for p in patterns) + ". Individual legitimate occupations are not themselves concerns; review the overall selection and context.",
            "Where appropriate, vary who performs technical, leadership and supporting work across teaching examples. Preserve factual accuracy and avoid assigning tasks by gender.")
    if report.findings:
        report.assessment = "repeated_pattern"
    return report
