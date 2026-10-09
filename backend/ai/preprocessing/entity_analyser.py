"""Reusable local NLP relationship extraction; no dataset access or bias verdicts."""

from functools import lru_cache

EXTRACTOR_VERSION = "care-nlp-2026-09-30.1"


@lru_cache(maxsize=1)
def load_local_pipeline():
    import spacy
    return spacy.load("en_core_web_sm")


class NLPAnalyser:
    """Shared document adapter; does not convert relationships into bias verdicts."""

    def __init__(self, nlp=None):
        self.nlp = nlp

    def analyse_document(self, document):
        from backend.ai.schemas.input_schema import Document
        from backend.ai.schemas.pipeline_schema import LayerResult, Relationship, evidence_for, source_id

        document = Document.model_validate(document)
        result = LayerResult(layer="nlp", metadata={"extractor_version": EXTRACTOR_VERSION},
                             limitations=["Relationships are linguistic evidence, not bias verdicts.",
                                          "Image descriptions are skipped; no actual image inspection is performed."])
        for section in document.sections:
            if section.type == "image_description":
                result.skipped_section_ids.append(section.section_id)
                continue
            if self.nlp is None:
                self.nlp = load_local_pipeline()
            result.metadata.update(model="en_core_web_sm", model_version=self.nlp.meta.get("version", ""))
            for item in extract_relationships(self.nlp(section.text)):
                evidence = evidence_for(section, item["start_char"], item["end_char"], item["sentence"])
                result.relationships.append(Relationship(
                    relationship_id=source_id("nlp", document.document_id, section,
                                              [item["type"], item["start_char"], item["evidence"]]),
                    relationship_type=item["type"], gender_term=item["gender_term"],
                    gender_reference=item["gender_reference"], related_term=item["related_term"],
                    predicate=item["predicate"], dependency_path=item["evidence"],
                    sentence_has_negation=item["sentence_has_negation"], action_object=item.get("action_object"), evidence=evidence))
            result.analysed_section_ids.append(section.section_id)
        if not result.analysed_section_ids:
            result.status = "skipped"
        return result

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
    "architect", "coder", "engineer", "engineering",
}
ABILITY = {
    "capable", "competent", "incompetent", "skilled", "skill", "ability",
    "competence", "capability", "talented", "proficient", "adept", "good",
    "well", "bad", "poor", "able", "unable", "excel", "excellent",
}
ATTRIBUTES = {
    "cautious", "confident", "confidence", "careful", "reckless", "timid",
    "assertive", "aggressive", "patient", "emotional", "logical", "analytical",
    "collaborative", "competitive", "shy", "outgoing", "risk", "caution",
    "willing", "reluctant", "methodical",
}
INTEREST = {
    "interest", "interested", "prefer", "preference", "enjoy", "like", "love",
    "dislike", "motivation", "motivated", "engaged", "enthusiastic", "attracted",
}
EVALUATION = {
    "incompetent", "competent", "good", "well", "bad", "poor", "excellent",
    "brilliant", "useless", "inferior", "superior", "incapable", "capable",
    "weak", "strong", "smart", "stupid", "talented", "impressive",
    "reliable", "unreliable",
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
        if word == "binary" and token.i >= 2 and doc[token.i - 2:token.i].text.lower() == "non-" and token.head.pos_ in {"NOUN", "PROPN"}:
            yield token, token.head
            continue
        # A surname such as "Professor Man" must not establish gender.
        if token.pos_ == "NOUN" and word in GENDER_NOUNS:
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
    elif anchor.dep_ in {"dobj", "obj", "dative"} and lemma(anchor.head) in {"assign", "appoint", "elect", "hire", "promote"}:
        yield anchor.head, "object recipient of occupational assignment"
    # Relative subjects refer back to this noun, not an arbitrary nearby person.
    for child in anchor.children:
        if child.dep_ == "relcl" and any(
            t.dep_ in SUBJECT_DEPS and t.lower_ in {"who", "which", "that"}
            for t in child.children
        ):
            yield child, "relative-clause subject refers to gender-associated noun"


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
    # Subject-control complements inherit the subject; object-control verbs
    # (e.g. 'women told men to be patient') deliberately do not.
    if lemma(predicate) in {"tend", "seem", "appear", "continue", "try"}:
        for child in predicate.children:
            if child.dep_ == "xcomp" and not any(t.dep_ in SUBJECT_DEPS for t in child.children):
                yield from attributed_concepts(child)
    if lemma(predicate) in {"perform", "do"}:
        yield from (t for t in predicate.children if t.dep_ == "advmod")
    if lemma(predicate) == "take":
        yield from (t for t in predicate.children if t.dep_ in {"dobj", "obj"} and lemma(t) == "risk")
    if lemma(predicate) in {"describe", "portray", "characterise", "characterize"}:
        for prep in predicate.children:
            if prep.dep_ == "prep" and lemma(prep) == "as":
                for child in prep.children:
                    if child.dep_ in {"pobj", "amod", "acomp"}:
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
            "gender_term": "non-binary" if lemma(gender) == "binary" and gender.i >= 2 and doc[gender.i - 2:gender.i].text.lower() == "non-" else gender.text,
            "gender_reference": nominal_phrase(anchor),
            "related_term": nominal_phrase(concept),
            "predicate": predicate_phrase(predicate),
            "evidence": f"{structure}; {dependency_path(gender, concept)}",
            "sentence": predicate.sent.text,
            "start_char": predicate.sent.start_char,
            "end_char": predicate.sent.end_char,
            "sentence_has_negation": any(t.dep_ == "neg" for t in predicate.sent),
            "action_object": " ".join(nominal_phrase(t) for t in predicate.children if t.dep_ in {"dobj", "obj"}) or None,
            "interpretation": "Potential CARE-relevant linguistic relationship detected",
        })

    for gender, anchor in gender_references(doc):
        # Inverted membership: 'Among the skilled technicians were women'.
        # Only a copular 'among' complement supports this attribution.
        if anchor.dep_ in COMPLEMENTS and lemma(anchor.head) == "be":
            for prep in anchor.head.children:
                if prep.dep_ == "prep" and lemma(prep) == "among":
                    for obj in prep.children:
                        if obj.dep_ == "pobj":
                            for concept in expand_nominal(obj):
                                for kind, vocabulary in [("P1", ROLES), ("P2", ABILITY), ("P3", ATTRIBUTES), ("P5", EVALUATION)]:
                                    if lemma(concept) in vocabulary:
                                        add(kind, gender, anchor, concept, anchor.head, "inverted copular group membership")
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

            # Evaluation relative to gender ('technical for a woman') or a
            # limiting judgement ('too emotional for leadership'). The adjective
            # is not intrinsically negative; the local construction supplies it.
            for concept in concepts:
                if concept.pos_ == "ADJ":
                    for prep in concept.children:
                        if prep.dep_ == "prep" and lemma(prep) == "for":
                            objects = [t for t in prep.children if t.dep_ == "pobj"]
                            gender_comparison = any(lemma(t) in GENDER_NOUNS for t in objects)
                            limitation = any(t.lower_ == "too" for t in concept.children)
                            if gender_comparison or limitation:
                                add("P5", gender, anchor, concept, predicate, "evaluative adjective with gender comparison or limiting degree")

            # Passive recipients of an assigned role; do not attribute a direct
            # object's role to the assigner in active clauses.
            if anchor.dep_ in {"nsubjpass", "dobj", "obj", "dative"} and lemma(predicate) in {"assign", "appoint", "elect", "hire", "promote"}:
                for obj in predicate.children:
                    roots = [obj] if obj.dep_ in {"dobj", "obj", "oprd"} else []
                    if obj.dep_ == "prep" and lemma(obj) in {"as", "to"}:
                        roots.extend(t for t in obj.children if t.dep_ == "pobj")
                    for root in roots:
                        for concept in expand_nominal(root):
                            if lemma(concept) in ROLES:
                                add("P1", gender, anchor, concept, predicate, "passive recipient of occupational assignment")
            # Career exclusion is still a relationship even in a rejected claim.
            if anchor.dep_ == "nsubjpass" and lemma(predicate) in {"discourage", "exclude", "bar", "prevent"}:
                for prep in predicate.children:
                    if prep.dep_ == "prep" and lemma(prep) == "from":
                        for obj in prep.children:
                            if obj.dep_ == "pobj":
                                for concept in expand_nominal(obj):
                                    if lemma(concept) in ROLES:
                                        add("P1", gender, anchor, concept, predicate, "occupational exclusion relationship")

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
            if predicate.pos_ == "VERB" and lemma(predicate) not in COPULAR_LEMMAS and anchor.dep_ not in {"dobj", "obj", "dative"}:
                add("P6", gender, anchor, predicate, predicate,
                    f"{subject_link}; person/group -> action/outcome verb")
    return matches


