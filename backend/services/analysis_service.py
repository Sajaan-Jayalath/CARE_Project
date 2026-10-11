"""Extracted text -> shared context windows -> layers -> document reconciliation."""
import hashlib
from copy import deepcopy

from backend.ai.analysis.care_engine import CAREEngine, default_factories
from backend.ai.analysis.result_merger import reconcile
from backend.ai.preprocessing.context_builder import build_context
from backend.ai.preprocessing.coverage import coverage_report
from backend.ai.llm.client import Settings, OllamaClient
from backend.ai.llm.analyser import LLMAnalyser
from backend.ai.analysis.document_analyser import analyse_document_patterns
from backend.ai.schemas.document_schema import DocumentPatternReport
from backend.ai.schemas.pipeline_schema import LayerResult, LayerError, PipelineResult


def analyse_extraction(extraction, engine=None, settings=None, on_progress=None, should_cancel=None, on_checkpoint=None, on_stage=None):
    response = {"status": "no_text", "extraction": extraction.model_dump(mode="json"),
                "analysis": None, "analysis_sources": {}, "chunks": [], "context_limitations": [],
                "coverage": coverage_report(extraction)}
    if extraction.document is None:
        return response
    settings = settings or Settings.from_env()
    document, sources, chunks = build_context(extraction, settings)
    if engine is None:
        # Load each layer once per document. A failed factory remains isolated by
        # CAREEngine, while successful NLP/model objects can be reused.
        instances = {}
        def cached(name, factory):
            def get():
                if name not in instances:
                    instances[name] = factory()
                return instances[name]
            return get
        factories = default_factories()
        factories["llm"] = lambda: LLMAnalyser(OllamaClient(settings), settings.max_input_chars)
        engine = CAREEngine({name: cached(name, factory) for name, factory in factories.items()})
    layers = {name: LayerResult(layer=name) for name in ("rules", "nlp", "llm")}
    records, seen = [], set()
    cancelled = False
    for index, chunk in enumerate(chunks, 1):
        if on_progress:
            on_progress(index, len(chunks))
        cancelled = cancelled or bool(should_cancel and should_cancel())
        try:
            if cancelled:
                raise InterruptedError()
            result = engine.analyse_document(chunk, on_progress=on_stage) if on_stage else engine.analyse_document(chunk)
        except Exception:
            code = "cancelled" if cancelled else "chunk_error"
            result = PipelineResult(document_id=document.document_id, input_sha256=hashlib.sha256(chunk.model_dump_json().encode()).hexdigest(),
                status="failed", layers={name: LayerResult(layer=name, status="skipped" if cancelled else "failed",
                    skipped_section_ids=[s.section_id for s in chunk.sections],
                    error=LayerError(code=code, message="This context window was not processed.")) for name in layers})
        chunk_ids = [s.section_id for s in chunk.sections]
        record = {"chunk": index, "section_ids": [s.section_id for s in chunk.sections],
                  "new_section_ids": [i for i in chunk_ids if i not in seen],
                  "context_section_ids": [i for i in chunk_ids if i in seen],
                  "input_sha256": result.input_sha256, "input_characters": len(chunk.model_dump_json()),
                  "input_bytes": len(chunk.model_dump_json().encode()),
                  "status": result.status, "reconciliation_status": result.reconciliation_status, "layers": {}}
        seen.update(chunk_ids)
        for name, contribution in result.layers.items():
            target = layers[name]
            windows = target.metadata.setdefault("assessment_windows", {})
            for concern in contribution.concerns:
                key = hashlib.sha256(concern.model_dump_json(exclude={"concern_id"}).encode()).hexdigest()
                windows.setdefault(key, []).append(index)
            target.concerns.extend(contribution.concerns)
            target.relationships.extend(contribution.relationships)
            target.analysed_section_ids.extend(contribution.analysed_section_ids)
            target.elapsed_seconds += contribution.elapsed_seconds
            target.limitations.extend(contribution.limitations)
            record["layers"][name] = {"status": contribution.status, "error": contribution.error.model_dump() if contribution.error else None,
                                      "analysed_section_ids": contribution.analysed_section_ids,
                                      "skipped_section_ids": contribution.skipped_section_ids,
                                      "metadata": contribution.metadata}
        records.append(record)
        if on_checkpoint and not cancelled:
            on_checkpoint(finalise_analysis(extraction, document, sources, chunks, layers, records, False))
    return finalise_analysis(extraction, document, sources, chunks, layers, records, cancelled)


def finalise_analysis(extraction, document, sources, chunks, layers, records, cancelled):
    """Build an immutable snapshot; incomplete checkpoints never claim completion."""
    layers = {name: layer.model_copy(deep=True) for name, layer in layers.items()}
    records = deepcopy(records)
    response = {"extraction": extraction.model_dump(mode="json")}
    ids = [s.section_id for s in document.sections]
    for name, layer in layers.items():
        covered = set(layer.analysed_section_ids)
        layer.analysed_section_ids = [i for i in ids if i in covered]
        layer.skipped_section_ids = [i for i in ids if i not in covered]
        layer.status = "completed" if covered else "failed"
        if not covered:
            layer.error = LayerError(code="all_chunks_failed", message=f"No successful {name} analysis is available.")
        layer.limitations = list(dict.fromkeys(layer.limitations))
        # Context overlap must not duplicate identical raw contributions. Keep
        # different assessments so Stage 3 can expose disagreements.
        layer.concerns = list({c.model_dump_json(exclude={"concern_id"}): c for c in layer.concerns}.values())
        layer.relationships = list({r.model_dump_json(exclude={"relationship_id"}): r for r in layer.relationships}.values())
        layer.metadata.update(chunk_count=len(chunks), coverage_basis="section processed successfully in at least one context window")
    any_success = any(l.status == "completed" for l in layers.values())
    complete = len(records) == len(chunks) and all(r["status"] == "completed" for r in records) and extraction.status == "completed" and not any(l.skipped_section_ids for l in layers.values())
    pipeline = PipelineResult(document_id=document.document_id, input_sha256=hashlib.sha256(document.model_dump_json().encode()).hexdigest(),
        status="completed" if complete else "partial" if any_success else "failed", layers=layers)
    pipeline.limitations[-1] = "Selectable text only; OCR and actual image/diagram interpretation were not performed. See extraction warnings."
    try:
        pipeline = reconcile(document, pipeline, merge_windows=True)
    except Exception:
        pipeline.reconciliation_status = "failed"
        pipeline.reconciliation_error = LayerError(code="reconciliation_error", message="Raw layer contributions are available but document reconciliation failed.")
        pipeline.status = "partial" if any_success else "failed"
    try:
        pipeline.document_analysis = analyse_document_patterns(document, sources, extraction, layers["nlp"])
    except Exception:
        pipeline.document_analysis = DocumentPatternReport(status="failed", assessment="unavailable",
            error="Document pattern analysis failed; passage-level results remain available.")
        pipeline.status = "partial" if any_success else "failed"
    limitations = ["Document patterns aggregate explicit NLP evidence across windows; unresolved identities and unsupported relationships remain unassessed.",
                  "Reading order is best effort; extracted text preview is not an exact rendering of the original document."]
    if len(chunks) > 1:
        limitations.append("Adjacent windows share a complete segment. Long sentences may span segments; relationships beyond a window still require educator review.")
    response.update(status=pipeline.status, analysis=pipeline.model_dump(mode="json"), analysis_sources=sources,
                    chunks=records, context_limitations=limitations,
                    analysis_document=document.model_dump(mode="json"),
                    cancelled=cancelled, coverage=coverage_report(extraction, document, sources, layers, records))
    response["coverage"]["planned_chunks"] = len(chunks)
    return response
