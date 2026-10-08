"""Run independent layers on unchanged content, then reconcile their findings."""
import hashlib
import time

from backend.ai.schemas.input_schema import Document
from backend.ai.schemas.pipeline_schema import LayerError, LayerResult, PipelineResult, evidence_for


def default_factories():

    from backend.ai.rules.rule_engine import RuleAnalyser
    from backend.ai.preprocessing.entity_analyser import NLPAnalyser
    from backend.ai.llm.analyser import LLMAnalyser
    return {"rules": RuleAnalyser, "nlp": NLPAnalyser, "llm": LLMAnalyser.from_env}


def validate_layer(result, document, name):
    if result.layer != name or result.status == "failed" or result.error is not None:
        raise ValueError("Invalid successful layer result")
    sections = {s.section_id: s for s in document.sections}
    analysed, skipped = result.analysed_section_ids, result.skipped_section_ids
    if len(set(analysed + skipped)) != len(analysed + skipped) or set(analysed + skipped) != set(sections):
        raise ValueError("Layer coverage is incomplete or duplicated")
    if (result.status == "completed") != bool(analysed):
        raise ValueError("Layer completion contradicts coverage")
    if (name == "nlp" and result.concerns) or (name != "nlp" and result.relationships):
        raise ValueError("Layer contributions have the wrong role")
    evidence = []
    for concern in result.concerns:
        if concern.source != {"rules": "Rules", "llm": "LLM"}.get(name):
            raise ValueError("Invalid concern source")
        if not concern.evidence or concern.location != concern.evidence[0].location or concern.original_content != concern.evidence[0].original_content:
            raise ValueError("Invalid primary concern evidence")
        evidence.extend(concern.evidence)
    evidence.extend(r.evidence for r in result.relationships)
    for item in evidence:
        location = item.location
        if location.section_id not in analysed:
            raise ValueError("Evidence refers to an unanalysed section")
        checked = evidence_for(sections[location.section_id], location.start_char, location.end_char, item.original_content)
        if checked != item:
            raise ValueError("Evidence metadata differs from source")


class CAREEngine:
    def __init__(self, factories=None):
        self.factories = default_factories() if factories is None else factories
        if set(self.factories) != {"rules", "nlp", "llm"}:
            raise ValueError("Provide factories for rules, nlp and llm")

    def analyse_document(self, document, on_progress=None):
        # Invalid user input fails before running any layer.
        document = Document.model_validate(document).model_copy(deep=True)
        layers = {}
        for name in ("rules", "nlp", "llm"):
            if on_progress:
                on_progress(name)
            started = time.monotonic()
            try:
                raw = self.factories[name]().analyse_document(document.model_copy(deep=True))
                if name == "llm":
                    from backend.ai.schemas.concern_schema import AnalysisResult
                    raw = AnalysisResult.model_validate(raw)
                    if raw.document_id != document.document_id:
                        raise ValueError("LLM returned a different document")
                    if raw.metadata.input_sha256 != hashlib.sha256(document.model_dump_json().encode()).hexdigest():
                        raise ValueError("LLM result belongs to a different document revision")
                    result = LayerResult(layer=name, concerns=raw.concerns,
                                         analysed_section_ids=raw.coverage.analysed_section_ids,
                                         metadata=raw.metadata.model_dump(mode="json"),
                                         limitations=raw.coverage.limitations)
                else:
                    result = LayerResult.model_validate(raw)
                validate_layer(result, document, name)
            except Exception as error:
                # No raw exception text: third-party libraries may include input
                # content or local paths. Keep completed sibling layers intact.
                from backend.ai.llm.client import LLMError
                code = error.code if isinstance(error, LLMError) else "insufficient_memory" if isinstance(error, MemoryError) else "layer_error"
                result = LayerResult(layer=name, status="failed",
                                     skipped_section_ids=[s.section_id for s in document.sections],
                                     error=LayerError(code=code, message=f"The {name} layer failed; no assessment from that layer is available."),
                                     metadata={"exception_type": type(error).__name__})
            result.elapsed_seconds = round(time.monotonic() - started, 4)
            layers[name] = result
        completed = sum(r.status == "completed" for r in layers.values())
        status = "completed" if completed == 3 and not any(r.skipped_section_ids for r in layers.values()) else "partial" if completed else "failed"
        result = PipelineResult(document_id=document.document_id,
                              input_sha256=hashlib.sha256(document.model_dump_json().encode()).hexdigest(),
                              status=status, layers=layers)
        try:
            from backend.ai.analysis.result_merger import reconcile
            if on_progress:
                on_progress("reconciliation")
            return reconcile(document, result)
        except Exception:
            # Reconciliation failure must not erase raw layer results or look
            # like a successful assessment with zero findings.
            result.reconciliation_status = "failed"
            result.reconciliation_error = LayerError(code="reconciliation_error", message="Could not reconcile findings; independent layer results remain available.")
            result.status = "partial" if completed else "failed"
            return result


def analyse_document(document):
    """Backend entry point returning a JSON-serialisable independent-layer result."""
    return CAREEngine().analyse_document(document).model_dump(mode="json")
