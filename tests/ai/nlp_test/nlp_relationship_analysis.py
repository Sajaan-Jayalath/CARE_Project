"""Stage 2: local CARE relationship extraction and development-set evaluation.

Run: python nlp_relationship_analysis.py
Requires locally installed spacy, en_core_web_sm, pandas and openpyxl.

This is a vocabulary-assisted dependency extractor, not a bias classifier.
Expected labels and CARE metadata are used only by the evaluation layer.
Limitations: finite concept coverage, parser errors, no name-based gender
inference, no general coreference, and no document-level interpretation.
Negated/quoted relationships remain linguistic relationships, not assertions.
"""

import json
from pathlib import Path
import re
import sys

import pandas as pd
import spacy


PROJECT_DIR = Path(__file__).resolve().parent
DATASET_PATH = PROJECT_DIR / "datasets" / "CARE_NLP_Relationship_Development_Dataset.xlsx"
RESULTS_PATH = PROJECT_DIR / "results" / "nlp_relationship_development_results.xlsx"
REQUIRED_COLUMNS = [
    "ID", "Test Text", "NLP Relationship Type", "CARE Category",
    "CARE Scenario ID", "Expected Relationship", "Reason",
]
RELATIONSHIP_TYPES = {
    "P1": "Gender ↔ Role/Occupation",
    "P2": "Gender ↔ Ability/Competence",
    "P3": "Gender ↔ Behaviour/Attribute",
    "P4": "Gender ↔ Interest/Motivation",
    "P5": "Gender ↔ Evaluation/Sexist Statement",
    "P6": "Gender ↔ Character/Outcome",
}

# Supporting concepts identify meaning only AFTER a grammatical link is found.
GENDER_NOUNS = {"woman", "man", "girl", "boy", "female", "male", "mother", "father"}
GENDER_PRONOUNS = {"she", "he", "her", "him", "hers", "his", "herself", "himself"}
GENDER_MODIFIERS = {"female", "male", "nonbinary", "transgender"}
ROLES = {
    "engineer", "programmer", "developer", "manager", "leader", "director",
    "scientist", "researcher", "technician", "analyst", "designer", "assistant",
    "secretary", "receptionist", "teacher", "professor", "nurse", "doctor",
    "administrator", "mentor", "tutor", "coordinator", "tester", "support",
}
ABILITY = {
    "capable", "competent", "incompetent", "skilled", "skill", "ability",
    "competence", "capability", "talented", "proficient", "adept", "good",
    "well", "bad", "poor", "able", "unable", "excel",
}
ATTRIBUTES = {
    "cautious", "confident", "confidence", "careful", "reckless", "timid",
    "assertive", "aggressive", "patient", "emotional", "logical", "analytical",
    "collaborative", "competitive", "shy", "outgoing", "risk", "caution",
}
INTEREST = {
    "interest", "interested", "prefer", "preference", "enjoy", "like", "love",
    "dislike", "motivation", "motivated", "engaged", "enthusiastic", "attracted",
}
EVALUATION = {
    "incompetent", "competent", "good", "well", "bad", "poor", "excellent",
    "brilliant", "useless", "inferior", "superior", "incapable", "capable",
    "weak", "strong", "smart", "stupid", "talented", "impressive",
}
LEARNING_OBJECTS = {"instruction", "example", "explanation", "diagram", "tutorial", "lesson"}
SUBJECT_DEPS = {"nsubj", "nsubjpass"}
NOMINAL_MODIFIERS = {"amod", "compound", "nmod", "poss"}
COMPLEMENTS = {"attr", "acomp", "oprd"}
COPULAR_LEMMAS = {"be", "become", "seem", "remain", "appear", "feel", "look"}
POSSESSION_LEMMAS = {"have", "possess", "demonstrate", "show", "display", "lack"}


def lemma(token):
    return token.lemma_.lower()


def nominal_phrase(token):
    """Keep noun modifiers without pulling in relative clauses or other subjects."""
    kept = {token.i: token}
    for child in token.children:
        if child.dep_ in NOMINAL_MODIFIERS:
            kept[child.i] = child
            for modifier in child.children:
                if modifier.dep_ in {"amod", "compound", "advmod"}:
                    kept[modifier.i] = modifier
    return " ".join(kept[index].text for index in sorted(kept))


def gender_references(doc):
    """Yield explicit gender token + its person/group anchor; names alone never qualify."""
    for token in doc:
        word = lemma(token)
        if token.pos_ in {"NOUN", "PROPN"} and word in GENDER_NOUNS:
            anchor = token.head if token.dep_ == "compound" else token
            yield token, anchor
        elif token.pos_ == "PRON" and word in GENDER_PRONOUNS:
            # A possessive such as 'her computer' does not gender the computer.
            if token.dep_ != "poss":
                yield token, token
        elif word in GENDER_MODIFIERS and token.dep_ in {"amod", "compound"}:
            if token.head.pos_ in {"NOUN", "PROPN"}:
                yield token, token.head


def predicates_for(anchor):
    """Direct subjects, shared-subject coordination, and explicit passive agents."""
    if anchor.dep_ in SUBJECT_DEPS:
        predicate = anchor.head
        yield predicate, anchor.dep_
        for child in predicate.children:
            if child.dep_ == "conj" and child.pos_ in {"VERB", "AUX", "ADJ"}:
                if not any(t.dep_ in SUBJECT_DEPS for t in child.children):
                    yield child, "shared subject of coordinated predicate"
    elif anchor.dep_ == "pobj" and anchor.head.dep_ == "agent":
        yield anchor.head.head, "explicit passive agent"


def expand_nominal(root):
    """Concept candidates within a complement, not arbitrary descendants."""
    yield root
    for child in root.children:
        if child.dep_ in {"amod", "compound", "nmod"}:
            yield child
        elif child.dep_ == "conj" and child.pos_ == root.pos_:
            yield from expand_nominal(child)


def attributed_concepts(predicate):
    """Only predicates/complements attributable to the subject are candidates."""
    yield predicate
    if lemma(predicate) in COPULAR_LEMMAS:
        for child in predicate.children:
            if child.dep_ in COMPLEMENTS:
                yield from expand_nominal(child)
    elif lemma(predicate) in POSSESSION_LEMMAS:
        for child in predicate.children:
            if child.dep_ in {"dobj", "obj"}:
                yield from expand_nominal(child)


def dependency_path(start, end):
    """Show the actual shortest tree path, including token indices and edge labels."""
    ancestors = {}
    token = start
    while True:
        ancestors[token.i] = token
        if token.head == token:
            break
        token = token.head
    right = []
    token = end
    while token.i not in ancestors:
        right.append(token)
        token = token.head
    common = token
    left = []
    token = start
    while token != common:
        left.append(f"{token.text}[{token.i}] -{token.dep_}-> ")
        token = token.head
    path = "".join(left) + f"{common.text}[{common.i}]"
    for token in reversed(right):
        path += f" <-{token.dep_}- {token.text}[{token.i}]"
    return path


def predicate_phrase(predicate):
    """Retain local negation, auxiliaries and adverbs without the whole clause."""
    tokens = [predicate] + [
        child for child in predicate.children
        if child.dep_ in {"aux", "auxpass", "neg", "advmod", "prt"}
    ]
    return " ".join(t.text for t in sorted(tokens, key=lambda t: t.i))


def extract_relationships(doc):
    """Return evidence dictionaries for all supported types, without dataset labels.

    The same text may yield several relationship types. No polarity, sexism,
    factual endorsement, or representation-imbalance verdict is produced.
    """
    matches = []
    seen = set()

    def add(kind, gender, anchor, concept, predicate, structure):
        key = (kind, gender.i, concept.i, predicate.i)
        if key in seen:
            return
        seen.add(key)
        matches.append({
            "type": kind,
            "gender_term": gender.text,
            "gender_reference": nominal_phrase(anchor),
            "related_term": nominal_phrase(concept),
            "predicate": predicate_phrase(predicate),
            "evidence": f"{structure}; {dependency_path(gender, concept)}",
            "sentence": predicate.sent.text,
            "sentence_has_negation": any(t.dep_ == "neg" for t in predicate.sent),
            "interpretation": "Potential CARE-relevant linguistic relationship detected",
        })

    for gender, anchor in gender_references(doc):
        # Explicit noun modification: 'female developer', even outside subject position.
        if anchor != gender and lemma(anchor) in ROLES:
            add("P1", gender, anchor, anchor, anchor, "gender modifier of role noun")

        # An attribute directly modifying an explicitly gender-associated person.
        for child in anchor.children:
            if child.dep_ == "amod":
                for kind, vocabulary in [("P2", ABILITY), ("P3", ATTRIBUTES), ("P5", EVALUATION)]:
                    if lemma(child) in vocabulary:
                        add(kind, gender, anchor, child, child, "attribute modifies gender-associated noun")

        for predicate, subject_link in predicates_for(anchor):
            concepts = list(attributed_concepts(predicate))
            for concept in concepts:
                for kind, vocabulary in [
                    ("P2", ABILITY), ("P3", ATTRIBUTES),
                    ("P4", INTEREST), ("P5", EVALUATION),
                ]:
                    if lemma(concept) in vocabulary:
                        add(kind, gender, anchor, concept, predicate,
                            f"{subject_link}; subject/predicate/complement attribution")

            # Roles require a copular complement or an 'as' complement of a role verb.
            if lemma(predicate) in COPULAR_LEMMAS:
                for concept in concepts:
                    if concept.pos_ == "NOUN" and lemma(concept) in ROLES:
                        add("P1", gender, anchor, concept, predicate, f"{subject_link}; role complement")
            if lemma(predicate) in {"work", "serve", "act"}:
                for prep in predicate.children:
                    if prep.dep_ == "prep" and lemma(prep) == "as":
                        for obj in prep.children:
                            if obj.dep_ == "pobj":
                                for concept in expand_nominal(obj):
                                    if lemma(concept) in ROLES:
                                        add("P1", gender, anchor, concept, predicate,
                                            f"{subject_link}; predicate -> as -> role object")

            # Learning-style preference needs a learning-related grammatical object.
            if lemma(predicate) in {"prefer", "like", "enjoy"}:
                for obj in predicate.children:
                    if obj.dep_ in {"dobj", "obj"} and lemma(obj) in LEARNING_OBJECTS:
                        add("P3", gender, anchor, obj, predicate,
                            f"{subject_link}; preference predicate -> learning object")

            # P6 records local actions as well as success/failure predicates.
            # No action vocabulary is needed: the syntactic verb link is evidence.
            if predicate.pos_ == "VERB" and lemma(predicate) not in COPULAR_LEMMAS:
                add("P6", gender, anchor, predicate, predicate,
                    f"{subject_link}; person/group -> action/outcome verb")
    return matches


def load_dataset(path):
    dataset = pd.read_excel(path, engine="openpyxl", keep_default_na=False)
    missing = set(REQUIRED_COLUMNS) - set(dataset.columns)
    if missing:
        raise ValueError(f"Missing dataset columns: {sorted(missing)}")
    if dataset.empty:
        raise ValueError("The development dataset is empty.")
    for index, row in dataset.iterrows():
        if not isinstance(row["Test Text"], str) or not row["Test Text"].strip():
            raise ValueError(f"Excel row {index + 2}: Test Text must be non-empty text.")
        if str(row["Expected Relationship"]).strip().lower() not in {"yes", "no"}:
            raise ValueError(f"Excel row {index + 2}: Expected Relationship must be Yes or No.")
        target_code(row["NLP Relationship Type"])
    return dataset


def target_code(value):
    match = re.match(r"^(?:NLP-)?(P[1-6])\b", str(value).strip())
    if not match:
        raise ValueError(f"Unsupported NLP Relationship Type: {value!r}")
    return match.group(1)


def evaluate_dataset(dataset, nlp):
    """Keep evaluation labels separate from the reusable document extractor."""
    rows = []
    for _, row in dataset.iterrows():
        doc = nlp(row["Test Text"])
        all_matches = extract_relationships(doc)
        target = target_code(row["NLP Relationship Type"])
        matches = [item for item in all_matches if item["type"] == target]
        expected = str(row["Expected Relationship"]).strip().title()
        detected = "Yes" if matches else "No"

        def joined(field):
            return " | ".join(dict.fromkeys(str(item[field]) for item in matches))

        rows.append({
            "Test ID": row["ID"],
            "Test Text": row["Test Text"],
            "Target NLP Relationship Type": row["NLP Relationship Type"],
            "CARE Category": row["CARE Category"],
            "CARE Scenario ID": row["CARE Scenario ID"],
            "Expected Relationship": expected,
            "Detected Relationship": detected,
            "Matched Gender Term": joined("gender_term"),
            "Matched Related Term": joined("related_term"),
            "Relationship / Predicate": joined("predicate"),
            "Linguistic Evidence": joined("evidence"),
            "Pass/Fail": "Pass" if detected == expected else "Fail",
            "Interpretation": (
                "Potential CARE-relevant linguistic relationship detected" if matches
                else "No supported target relationship extracted; this is not a bias verdict"
            ),
            "Reason": row["Reason"],
            "All Detected Types": ", ".join(sorted({item["type"] for item in all_matches})),
            "Target Evidence JSON": json.dumps(matches, ensure_ascii=False),
            "Dependency Diagnostics": " | ".join(
                f"{t.text}[{t.i}] lemma={t.lemma_} POS={t.pos_} "
                f"-{t.dep_}-> {t.head.text}[{t.head.i}]" for t in doc
            ),
            "spaCy Version": spacy.__version__,
            "Model Version": nlp.meta.get("version", ""),
        })
    return pd.DataFrame(rows)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        dataset = load_dataset(DATASET_PATH)
        nlp = spacy.load("en_core_web_sm")  # Local model only; never download.
        results = evaluate_dataset(dataset, nlp)
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        results.to_excel(RESULTS_PATH, index=False, engine="openpyxl")
    except (OSError, ValueError, ImportError) as error:
        raise SystemExit(f"Stage 2 could not complete: {error}") from error

    passed = results["Pass/Fail"].eq("Pass")
    expected_yes = results["Expected Relationship"].eq("Yes")
    print("Stage 2 development checks — not overall CARE accuracy")
    print(f"Total test cases: {len(results)}")
    print(f"Passed: {int(passed.sum())}")
    print(f"Failed: {int((~passed).sum())}")
    print(f"Expected Yes correctly detected: {int((passed & expected_yes).sum())}")
    print(f"Expected No correctly rejected: {int((passed & ~expected_yes).sum())}")
    failed_ids = results.loc[~passed, "Test ID"].astype(str).tolist()
    print("Failed test IDs: " + (", ".join(failed_ids) if failed_ids else "None"))
    print(f"Results saved to: {RESULTS_PATH}")
    print("Local linguistic evidence only; no bias verdict or representation-imbalance claim.")


if __name__ == "__main__":
    main()

