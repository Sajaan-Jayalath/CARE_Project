"""Visual Representation review of actual images with the local vision model.

One request per image returns structured observations (who is visible, presented how,
doing what) and any concerns, each tied to observed people. Concerns that do not rest
on the observations are dropped and counted, never shown. Patterns that only appear
across several images are counted separately (visual_patterns).
"""
import base64
import hashlib
import json
from typing import Literal

from pydantic import Field

from backend.ai.framework.categories import load_framework
from backend.ai.llm.client import LLMError, OllamaClient, Settings
from backend.ai.schemas.input_schema import NonBlank, StrictModel
from backend.ai.vision.image_context import VISION_PROMPT_VERSION, build_vision_prompt, criteria_names

Level = Literal["Low", "Medium", "High"]
# Criteria judged from people in the picture; the other two (colour coding, environment) may not involve people.
PEOPLE_CRITERIA = {"Stereotypical Imagery", "Gendered Visual Authority", "Technical Confidence Portrayal",
                   "Sexualised/Objectifying Imagery"}


class Person(StrictModel):
    id: NonBlank
    presentation: Literal["woman", "man", "unclear"]
    role: Literal["technical", "leadership", "supporting", "learner", "other"]
    activity: str = Field(max_length=200)
    prominence: Literal["main", "secondary", "background"]


class VisualConcern(StrictModel):
    criterion: NonBlank
    person_ids: list[str] = Field(max_length=20)
    severity: Level
    confidence: Level
    explanation: NonBlank
    recommendation: NonBlank


class VisualReview(StrictModel):
    image_type: Literal["photo", "illustration", "diagram_or_chart", "screenshot", "icon_or_logo", "other"]
    summary: str = Field(max_length=600)
    visible_text: str = Field(max_length=600)
    people: list[Person] = Field(max_length=20)
    concerns: list[VisualConcern] = Field(max_length=6)


class VisionAnalyser:
    def __init__(self, client: OllamaClient, framework=None):
        self.client = client
        self.framework = framework or load_framework()
        self.prompt = build_vision_prompt(self.framework)
        # Models vary quotes, slashes and case in criterion names: match on letters only.
        self.criteria = {_key(name): name for name in criteria_names(self.framework)}

    @classmethod
    def from_env(cls):
        return cls(OllamaClient(Settings.from_env()))

    def analyse(self, image, context=""):
        """Review one FoundImage. Returns a JSON-ready dict; raises LLMError on model failure."""
        user = json.dumps({"image": image.label, "nearby_text": context[:600]}, ensure_ascii=False)
        review = self.client.structured(self.prompt, user, VisualReview, images=[base64.b64encode(image.png).decode()])
        people = {p.id: p for p in review.people}
        concerns, dropped = [], 0
        for concern in review.concerns:
            ids = [i for i in dict.fromkeys(concern.person_ids)]
            criterion = self.criteria.get(_key(concern.criterion))
            grounded = (criterion is not None and all(i in people for i in ids)
                        and (criterion not in PEOPLE_CRITERIA
                             or any(people[i].presentation != "unclear" for i in ids)))
            if grounded:
                concerns.append(concern.model_dump() | {"person_ids": ids, "criterion": criterion})
            else:
                dropped += 1
        return {"image_id": image.image_id, "status": "analysed", "image_type": review.image_type,
                "summary": review.summary, "visible_text": review.visible_text, "people": [p.model_dump() for p in review.people],
                "concerns": concerns, "dropped_concerns": dropped}


def _key(name):
    return "".join(c for c in name.lower() if c.isalnum())


def describe(person):
    shown = {"woman": "a person presented as a woman", "man": "a person presented as a man"}.get(person["presentation"], "a person")
    return f"{shown}: {person['activity']}" if person.get("activity") else shown


def _finding_id(*parts):
    return "vis-" + hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:20]


def visual_findings(document_id, images, reviews):
    """One Visual Representation finding per grounded concern, attached to its image."""
    meta = {i.image_id: i for i in images}
    out = []
    for review in reviews:
        if review.get("status") != "analysed":
            continue
        image = meta[review["image_id"]]
        people = {p["id"]: p for p in review["people"]}
        for concern in review["concerns"]:
            observed = [describe(people[i]) for i in concern["person_ids"]]
            out.append({
                "finding_id": _finding_id(document_id, image.sha256, concern["criterion"], concern["person_ids"]),
                "category": "Visual Representation", "sources": ["Vision"], "scope": "image",
                "criterion": concern["criterion"], "images": [image.meta()],
                "observations": observed or [review["summary"]], "evidence": [], "contributions": [],
                "supporting_relationships": [], "explanation": concern["explanation"],
                "recommendation": concern["recommendation"], "severity": concern["severity"],
                "severity_basis": "Severity supplied by the local vision model for this image; a review aid, not a measurement.",
                "confidence": concern["confidence"],
                "confidence_basis": "Qualitative vision-model judgement; visual interpretation requires educator review.",
                "review_status": "needs_contextual_review", "review_reasons": ["visual_interpretation"]})
    return out


def visual_patterns(document_id, images, reviews, minimum_examples=3, dominance=0.8):
    """Repeated gendered role portrayal across images (same thresholds as text patterns)."""
    meta = {i.image_id: i for i in images}
    roles = {"technical": "Stereotypical Imagery", "supporting": "Stereotypical Imagery", "leadership": "Gendered Visual Authority"}
    findings = []
    for role, criterion in roles.items():
        seen = {"woman": set(), "man": set()}
        for review in reviews:
            if review.get("status") != "analysed":
                continue
            for person in review["people"]:
                if person["role"] == role and person["presentation"] in seen:
                    seen[person["presentation"]].add(review["image_id"])
        total = len(seen["woman"]) + len(seen["man"])
        if not total:
            continue
        group = max(seen, key=lambda g: len(seen[g]))
        count = len(seen[group])
        if count < minimum_examples or count / total < dominance:
            continue
        ids = sorted(seen[group], key=lambda i: int(i[3:]))
        noun = {"technical": "technical work", "supporting": "supporting work", "leadership": "leading or presenting"}[role]
        findings.append({
            "finding_id": _finding_id(document_id, "pattern", role, ids), "category": "Visual Representation",
            "sources": ["Vision"], "scope": "document_pattern", "criterion": criterion,
            "images": [meta[i].meta() for i in ids],
            "observations": [f"{meta[i].label}: {'people presented as women' if group == 'woman' else 'people presented as men'} shown doing {noun}"
                             for i in ids],
            "evidence": [], "contributions": [], "supporting_relationships": [],
            "explanation": (f"Across the images, people shown doing {noun} are presented as "
                            f"{'women' if group == 'woman' else 'men'} in {count} of {total} images where this role appears. "
                            "This is a repeated visual pattern to review, not proof of bias or a statement about anyone's identity."),
            "recommendation": f"Where appropriate, vary who is pictured doing {noun} across the teaching material.",
            "severity": "Medium", "severity_basis": "Repeated visual imbalance is reviewed at Medium priority by policy.",
            "confidence": "Low", "confidence_basis": "Counts rest on how people are visually presented, which can be uncertain.",
            "review_status": "needs_contextual_review", "review_reasons": ["visual_interpretation", "pattern_requires_contextual_review"],
            "statistics": {"role": role, "counts": {g: len(v) for g, v in seen.items()}}})
    return findings


def analyse_images(images, analyser_factory, on_progress=None, should_cancel=None, context=None):
    """Review every image; one failure is recorded and the rest continue."""
    reviews, analyser = [], None
    for index, image in enumerate(images, 1):
        if should_cancel and should_cancel():
            break
        if on_progress:
            on_progress(index, len(images))
        try:
            analyser = analyser or analyser_factory()
            reviews.append(analyser.analyse(image, (context or {}).get(image.image_id, "")))
        except LLMError as error:
            reviews.append({"image_id": image.image_id, "status": "failed", "error": {"code": error.code, "message": str(error)}})
        except Exception:
            reviews.append({"image_id": image.image_id, "status": "failed",
                            "error": {"code": "vision_error", "message": "This image could not be reviewed."}})
    return reviews
