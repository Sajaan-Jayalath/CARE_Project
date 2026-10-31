# Stage 5 — long documents and context

Stage 6 now adds [document-level pattern aggregation](stage6_document_analysis.md)
over these windows. The context limitations below describe the Stage 5 baseline.

Stage 4 supplied the initial chunked text pathway. Stage 5 strengthens it without
changing supported formats, the 20 MB/100-page policy, or the local-only model.

## Implemented

- **Budgeted segmentation:** each segment reserves sufficient serialized space
  for a neighbouring segment. Budget checks include UTF-8 bytes, section metadata,
  the framework, JSON schema, output tokens and the evidence-repair prompt.
  Paragraph, sentence and word boundaries are preferred; oversized unbroken tokens
  can require a character split. Original text and source offsets stay intact.
- **Guaranteed boundary overlap:** adjacent windows share a complete segment,
  so text immediately across a window boundary is presented together in at least
  one call. Invalid budgets fail before inference, rather than truncating text.
  First/last windows naturally have only one external neighbour. This provides
  local context, not unlimited whole-document reasoning.
- **Shared settings:** the chunk planner and actual LLM use the same settings,
  including when configuration is supplied programmatically.
- **Reconciliation across windows:** identical assessments are deduplicated.
  Assessments from different windows with the same layer, category, exact evidence
  positions and recommendation (ignoring whitespace differences) are grouped;
  all differing explanations/severity/confidence remain as contributions, with
  conflicts flagged. Different source occurrences and independently supported
  categories remain separate. Broader/narrower evidence or different suggestions
  remain marked possible duplicates for human review. Distinct assessments made
  within the same window are not automatically collapsed.
- **Scalable reconciliation:** candidate comparisons are indexed by source section
  instead of comparing every finding with every unrelated document finding.
- **Failure isolation:** an unexpected whole-window failure does not discard
  previous or subsequent results. Optional `should_cancel` supports cancellation
  between inference windows; no claim is made of interrupting an active model call.
  Cancellation controls in the final website are future frontend/job work.

## Coverage returned to the caller

The response now includes `coverage` even when extraction finds no selectable text:

- Original-section and canonical-segment counts.
- Per-layer processed-character counts (overlap is counted once).
- Per-original-section status: `completed`, `partial`, or `unprocessed`.
- Exact processed/unprocessed source ranges and source locations for each layer.
- Failed/unprocessed window IDs, extraction warnings, and whitespace-only ranges
  deliberately excluded from inference. Unreadable/scanned pages stay visible
  through their extraction warning and page location; they cannot have fabricated
  character ranges when no text was extractable.

Each `chunks` record includes its input hash/size, new vs overlapping section IDs,
and each layer's analysed/skipped sections and error. A window failure remains
visible even if overlap allowed another window to process all of its text. The
overall result is still partial in that case because contextual assessment failed.
Root-layer `completed` means some successful contribution; consult coverage and
overall status for completeness. `cancelled` distinguishes an interrupted run.

## Testing and use

The Stage 4 API and CLI automatically use the improved pipeline:

```powershell
.\.venv\Scripts\python.exe -m backend.extraction --file "path\to\document.docx" --analyse --output output/document-result.json
.\.venv\Scripts\python.exe -m pytest tests -q
```

Stage 5 tests cover long paragraphs, Unicode/emoji, unbroken tokens, a 300,000-
character / 100-generated-page input, overlap guarantees, input budgets,
cancellation, window failures, source coverage, extraction warnings, repeated
assessments and preserved disagreements. The 100-page test validates planning and
coverage without running hundreds of model requests. It is not a 100-page model
accuracy or latency benchmark.

The live local check is recorded in `output/stage5-live.json` (ignored by Git).
Verification: **180 tests passed** across the full suite. The live local run
completed two windows and all three layers covered all four canonical segments;
it returned one reconciled wording finding. The two test warnings are existing
Starlette test-client dependency deprecations.
Long documents require more local inference calls and can take substantial time.
These changes do not guarantee detection of every bias, resolve all references
outside the context window, or implement document-wide statistical representation
analysis. OCR and actual visual analysis remain outside this text stage.
