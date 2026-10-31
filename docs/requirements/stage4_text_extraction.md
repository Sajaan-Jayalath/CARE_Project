# Stage 4 — text upload, extraction and AI integration

Implemented 2026-09-30. This stage covers **text only**. It does not implement
OCR, visual inference, the React preview, persistent history, or background jobs.

The chunking and coverage details below describe the Stage 4 baseline.
[Stage 5](stage5_long_documents.md) supersedes optional overlap with reserved,
guaranteed boundary overlap and adds detailed source-range coverage.
[Stage 8](stage8_backend.md) supersedes the synchronous HTTP analysis endpoints
below with persistent background jobs, results and feedback. CLI behaviour remains
synchronous. `/docs` now serves an offline endpoint reference.

## Workflow

Pasted text / uploaded file → size and format validation → extraction with source
locations → non-destructive preprocessing → bounded context windows → rules,
NLP and local LLM on identical window text → document reconciliation → findings,
coverage and source maps returned to the caller.

Dependencies are in `requirements.txt`; install with the project Python:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m backend.extraction --file sample_data/text/teaching_example.docx --analyse --output output/document-result.json
.\.venv\Scripts\python.exe -m backend.extraction --text "Every programmer should test his code." --analyse
```

Omit `--analyse` to inspect extraction without loading any AI layer. No files are
saved unless `--output` is supplied. An unavailable LLM produces partial analysis
with successful rules/NLP retained. Exit codes: 0 completed, 1 partial/no-text/
failed analysis, 2 input/configuration/I/O failure.

## Supported text and known boundaries

| Format | Extracted content and source information |
| --- | --- |
| Pasted text, TXT, MD | Original wording/whitespace, blank-line paragraphs, input character offsets. Markdown remains source text; it is not interpreted as HTML. |
| PDF | Selectable text blocks in approximate top-to-bottom/left-to-right order; native page, block box, detected table grid metadata. No table text is emitted twice. Complex columns and font encodings need review. |
| DOCX | Body paragraphs, hyperlink text, nested table text, Word text boxes, DrawingML text, headers, footers, footnotes/endnotes, captions stored as normal text. XML paths identify exact source elements. Deleted tracked text and field instructions are excluded; cached displayed field results are retained. Headers/footers are extracted once per part, not repeated on every page. |
| PPTX | Slide shape paragraphs, grouped shapes, table cells, chart title/series/category labels where supported, speaker notes. Slide, shape, group path and shape boxes are retained. Template/master placeholders are excluded and reported. Group boxes use their parent coordinate system. |
| XLSX, XLSM | Sheet titles; populated cells including hidden sheets/rows/columns; shared/inline strings and cached formula results. Sheet/cell addresses retained. Drawing text extracted where available, with visual/chart limitations reported. Formulas, external links and macros never execute. |
| XLS | Legacy OLE workbook sheet titles and cached cell values. Drawings, charts and comments are not extracted; limitation explicitly reported. |
| CSV, TSV | Parsed cell text, logical row/column. Quoted multiline cells preserved. Coordinates refer to parsed cells rather than CSV file byte offsets. |

Plain text supports UTF-8 (with/without BOM) and BOM-marked UTF-16. Other encodings
require conversion. Legacy DOC/PPT, HTML, RTF and other unlisted formats are
rejected rather than assumed to work. Office support targets ordinary OOXML
files; unsupported structures are either reported or rejected. This is not a
guarantee to extract every proprietary object in every Office document.

Image uploads are rejected for now. Embedded images, PDF vector graphics and
image-only pages are marked unassessed. Captions present as selectable text are
extracted; words drawn inside images await OCR. Empty/image-only documents return
`no_text` with no AI assessment. Warnings make extraction/overall analysis partial,
including informational limitations that require reviewing the original.

## Limits and pagination

- 20,000,000 bytes per file; 100 native PDF pages or PPTX slides.
- Word/Excel/plain text have no reliable native page count here. Stage 4 uses a
  deterministic **generated text preview** of 3,000 extracted characters per page,
  maximum 100 pages. These are NOT Microsoft Word/Excel print pages. This is an
  interim operational policy until the frontend rendering stage; native Office
  pagination is not implemented. The rule includes hidden/populated content and
  ignores print areas. Preview offsets concatenate extracted sections in order.
- Maximum 1,000,000 extracted characters and 20,000 source sections; for generated
  previews the 100-page/300,000-character limit is stricter.
- Office ZIPs: at most 10,000 members / 100 MB declared expanded data; duplicate,
  traversal and encrypted entries rejected; XML entity expansion disabled. Files
  are read in memory, not unpacked into the repository. Multipart request bodies
  have a 21 MB cap including overhead; the actual file still has a 20 MB cap.
- Corrupted/password-protected documents return explicit input errors. A repaired
  PDF is marked partial. No passwords, macro execution or network fetching.

## Context and source mapping

`cleaner.py` validates and copies text without stripping pronouns, lowercasing or
rewriting punctuation. `segmenter.py` chooses paragraph/sentence/word boundaries
for long sections. Very long unbroken tokens may need a character boundary.

`context_builder.py` calculates the same conservative UTF-8 byte/context budget
as the local client, including framework, JSON schema, output reserve and evidence
repair instructions. It splits source units only as necessary and packs adjacent
units into windows. One previous section overlaps when space permits. Every
non-whitespace text segment is covered; no over-budget text is silently truncated.
Whitespace-only spans need no AI assessment. Window-local context cannot establish
all document-wide representation patterns or resolve references outside the window.

Every window uses stable section IDs, identical across all three layers. Raw
contributions are aggregated, exact overlap duplicates removed, and Stage 3
reconciliation runs over the combined results. Different assessments remain for
review. Each chunk retains model/framework metadata and each layer's error/status.
The aggregate layer `completed` means it supplied some valid results; consult
`skipped_section_ids`, per-chunk records and overall status for full coverage.
The overall result is partial if any window/layer/extraction step is partial.

The response contains:

- `extraction.document`: unchanged extracted preview sections.
- `extraction.sources`: page/slide/table/cell/part/box information per section.
- `analysis_document`: the canonical segmented input used across windows.
- `analysis_sources`: segment → original section plus `source_start_char` and
  `source_end_char`, and original file provenance.
- `analysis`: reconciled findings and raw layer contributions.
- `chunks`: success/failure, section coverage and model metadata per window.
- `context_limitations`: explicit limits on global/visual interpretation.

For a finding's evidence, look up `analysis_sources[evidence.location.section_id]`.
Find its original section in `extraction.document.sections`. The exact highlight
range in that original section is:

```text
start = source_start_char + evidence.location.start_char
end   = source_start_char + evidence.location.end_char
original_section.text[start:end] == evidence.original_content
```

Offsets use Python Unicode code points, end-exclusive. A React client must convert
to JavaScript UTF-16 indices (or slice `Array.from(text)`) for non-BMP characters.
For pasted/plain text, add `input_start_char` to navigate the original full input.
PDF boxes describe a block, not exact character boxes. Full visual preview and
precise native-document highlighting remain frontend/rendering work. Always render
extracted text as text, never trusted HTML.

## Local API

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs` for API testing. This is the backend's automatic
documentation, not a new testing website or the final React interface.

| Endpoint | Input | Behaviour |
| --- | --- | --- |
| GET `/health` | None | Backend liveness; does not claim Ollama/model health. |
| POST `/api/extract/text` | JSON `{"text":"..."}` | Extract/preview only. |
| POST `/api/extract/file` | Multipart `file` | Extract/preview only. |
| POST `/api/analysis/text` | JSON `{"text":"..."}` | Full text pipeline. |
| POST `/api/analysis/file` | Multipart `file` | Full document text pipeline. |

One inference request runs per process; another receives 409. Run one local
Uvicorn worker. Requests run extraction/analysis off the event loop but remain
synchronous HTTP requests; long documents can take many model calls. Job polling,
cancellation, authentication, deployment hardening, history and a final frontend
are later stages. Uploaded bytes are not persistently saved (multipart may use
temporary OS spooling, closed after reading).

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Tests cover real generated PDF/DOCX/PPTX/XLSX fixtures, a saved XLS fixture,
plain-text/tabular input, native/generated limits, unsafe Office XML/ZIP paths,
source mapping, Unicode, context budgets, overlap deduplication, partial failures,
and actual HTTP extraction/analysis routing. Mock LLM tests validate orchestration,
not model detection accuracy. Live local results are stored under ignored `output/`.
No claim is made that every gender-bias issue is detected.

Verification on 2026-09-30: **170 tests passed** across the complete suite (34
Stage 4 tests plus 136 existing tests). Two dependency deprecation warnings came
from Starlette's HTTP test client; no test failures. Live Ollama checks completed
for pasted text, the sample DOCX, and a two-window longer passage, with all three
layers successful. Local reports: `output/stage4-live-text.json`,
`output/stage4-live-docx.json`, `output/stage4-live-chunks.json`. The model can group
separate wording issues into one broad finding; ambiguous overlaps are retained
for review by Stage 3 rather than falsely treated as unique issue counts.

Sample files in `sample_data/text/` are synthetic teaching text. The XLS fixture
was generated once with xlwt 1.3.0; xlwt is not a runtime dependency.
