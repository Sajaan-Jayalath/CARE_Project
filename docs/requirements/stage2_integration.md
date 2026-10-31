# Stage 2: independent AI layer integration

Stage 3 now adds reconciliation after the independent layer execution described
here. The historical Stage 2 output boundary below is superseded by
[the Stage 3 contract](stage3_reconciliation.md); raw layer results are still retained.

Implemented 2026-09-30. Entry point: `CAREEngine.analyse_document(Document)` in
`backend/ai/analysis/care_engine.py`. A convenience function returns a JSON-ready
dictionary. The engine does not require a web server or database.

## Behaviour

1. Validate the shared `Document` once and retain an unchanged copy.
2. Run rule, NLP and LLM adapters sequentially, each receiving its own deep copy
   of the same document. The LLM sees original content, not sibling predictions.
3. Validate contribution provenance, source spans and section coverage.
4. Return one envelope containing separate `rules`, `nlp`, and `llm` results.

Sequential orchestration is deliberate for this local baseline: concurrency,
job cancellation and scheduling belong to the application backend stage.

The reusable NLP algorithm now lives in
`backend/ai/preprocessing/entity_analyser.py`; its existing evaluation script
imports that implementation. Production code never imports the testing folder.
spaCy loads lazily from the installed local English model and is cached within
the process. Missing model/dependency errors affect only the NLP layer.

## Contract

- Shared input retains document identity, section IDs, unchanged text, section
  type, page, slide and paragraph. Offsets are Python Unicode character offsets
  within a section, with an exclusive end. This stage does not invent spreadsheet
  cells, image bounding boxes or document extraction locations.
- Rule and LLM outputs are concerns, each with `source`, evidence and stable ID.
  Rule severity/confidence remain null because they are not assessed by the
  existing detector. The LLM model-response contract still requires both fields.
- NLP outputs are typed relationships with source evidence and dependency
  information. They never populate the concern list automatically.
- Each layer records status, elapsed time, analysed/skipped section IDs,
  limitations, metadata and a structured error when it fails.
- The root input hash identifies the exact document revision. LLM input hashes
  and every returned evidence span are checked against it/the source document.
- Successful siblings survive a failed layer. Raw exception messages are not
  exposed because libraries may include teaching text or local paths.

`completed` means all three layers completed across all supplied sections, not
that all possible bias was detected. `partial` means at least one layer completed
and another failed or skipped content. `failed` means no layer completed.
Empty concern lists are valid results only from a completed layer.

Rules and NLP process text and OCR text. They skip image descriptions because
description wording is not the original visual content. The LLM can assess supplied
descriptions under its existing contract; actual image inspection remains Stage 7.
Description-only/mixed inputs therefore explicitly expose partial layer coverage.

## Run

From the project root with Ollama running:

```powershell
.\.venv\Scripts\python.exe -m backend.ai.analysis --text "Every engineer should check his work."
.\.venv\Scripts\python.exe -m backend.ai.analysis --input sample_data/llm/stage2_document.json --output output/stage2_result.json
.\.venv\Scripts\python.exe -m pytest tests -q
```

The CLI accepts pasted text or shared Document JSON, not a PDF/Office upload yet.
It exits 0 for completed, 1 for partial/failed analysis, and 2 for invalid input or
file access errors. Partial results are written when `--output` is supplied.
The existing LLM CLI remains an independent LLM testing tool.

## Stage boundary

There is deliberately no aggregate unique-concern count or merged list yet.
`reconciliation_status` is `not_performed`: duplicates, disagreements and separate
category evidence remain visible for Stage 3 to reconcile. Stage 2 does not improve
classification accuracy or remove a valid rule finding because the LLM is silent.
Long-input LLM limits still apply; rejection becomes a partial pipeline result,
not silent truncation. Extraction, actual vision and the React/FastAPI application
are later stages.

## Verification

Integration tests cover genuine rule/NLP calls, source locations, Unicode, stable
IDs, independent input copies, invalid evidence, invalid coverage, invalid inputs,
missing/failed layers, LLM size limits, stale document revisions and skipped visual
scope. Existing independent-layer tests remain in place.

NLP dataset checks after moving the implementation reproduced 24/24 development,
24/24 controlled and 23/24 challenge matches. The preserved S4-P6-03 label conflict
is not hidden. Reports and a real Ollama end-to-end result are saved under
`evaluation/results/stage2/`. That live result demonstrates execution, not perfect
classification: inspect its explanations before accepting findings.
