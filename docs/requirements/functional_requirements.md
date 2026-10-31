# CARE first-release functional requirements

Stage 1 baseline, recorded 2026-09-30. This document describes required behaviour,
not functionality already implemented. User-confirmed scope is distinguished
from proposed operational defaults and unresolved decisions below.

Stage 4 implementation note: the text pathway now supports the formats and
interim generated pagination documented in [the Stage 4 guide](stage4_text_extraction.md).
Native Office rendering, OCR/vision and frontend preview remain pending. The
proposed final-release pagination below is not a claim of current support.

## Confirmed scope

- An English-language React website with a locally running Python FastAPI backend.
- Analyse pasted text, PDF, PPTX, DOCX, Excel workbooks, and image uploads.
- Maximum upload size: 100 MB per file. Maximum document length: 100 pages.
- Analyse text, scanned/image text through local OCR, and actual visual content.
- Identify potential gender-inclusivity concerns, explain each finding, recommend
  an inclusive improvement, and flag the relevant content in a document preview.
- All inference runs locally. Downloaded models are permitted; third-party AI
  services are not. Local browser/backend/Ollama communication is permitted.
- The five CARE categories are Gendered Language, Gender Stereotypes,
  Representation, Visual Representation, and Professional Roles.
- The user will supply a frontend prototype during the frontend stage. Detailed
  visual design must follow that prototype when available.

## Input support and limits

The user requested all image formats. A finite, testable compatibility matrix is
needed: universal support for every image codec, camera RAW format, and proprietary
editor format is not an implementable guarantee. The following is the proposed
release matrix, to be confirmed before extraction implementation.

| Input | Proposed release support | Source locations |
| --- | --- | --- |
| Text | Pasted English text | Paragraph and character offsets |
| PDF | Text PDFs, scanned PDFs, mixed text/image PDFs | Page, text ranges, bounding boxes |
| Word | DOCX, including tables and embedded images | Stable rendered page, paragraph/table cell, bounding boxes |
| Slides | PPTX, including notes, tables, and images | Slide, shape/text range, image region; notes identified separately |
| Excel | XLSX, XLS, XLSM; read content without executing macros | Workbook, sheet, cell/range, embedded image/chart |
| Raster images | PNG, JPG/JPEG, WebP, BMP, GIF, TIF/TIFF, AVIF, HEIC/HEIF, ICO | Frame/page and bounding boxes |
| Vector images | SVG, rendered locally with scripts and external resources disabled | Rendered image region |

Extension aliases must be recognised by detected content type, not extension
alone. RAW, PSD and other unlisted formats remain a compatibility decision, not
silently supported. Legacy DOC/PPT files are not yet confirmed requirements.

Proposed consistent counting rules:

- Interpret 100 MB as 100,000,000 bytes; enforce at upload and processing boundaries.
- PDF: one PDF page is one page. DOCX: use the same locally rendered pagination
  for preview, source mapping, and the page limit.
- PPTX: one slide counts as one page; include its notes in the processing budget.
- Excel: count pages in a CARE-generated, stable preview with populated content
  included. Ignore user-defined print areas that would conceal populated cells.
  A sheet is not necessarily one page. Preserve cell addresses alongside pages.
- Images: one still image is one page; each TIFF page, animation frame, HEIF image,
  or ICO image selected for analysis counts toward the limit. Never silently
  analyse only the first frame/image of a multi-image upload.
- Maximum 100 such pages per file. Embedded images on a document page do not add
  document pages, but do count toward processing resource budgets.
- Proposed first-release interaction: one document per scan, with pasted text as
  an alternative. Multi-file batch analysis is not yet required.
- Apply additional declared decoded-image and extracted-text budgets from the
  non-functional requirements. Do not truncate accepted content silently.

## Required user workflow and acceptance criteria

| ID | Requirement | Acceptance check |
| --- | --- | --- |
| FR-01 | Accept pasted text or a supported upload and validate it before analysis. | Accept fixtures for each supported format; reject oversized, overlength, mismatched, malformed and unsupported files with an actionable message. |
| FR-02 | Extract text, tables, sheet/cell information, slide notes, images and captions while preserving provenance. | Every extracted item can be traced back to the original file; extraction failures are reported by page/section. |
| FR-03 | Apply local OCR to scanned/image text and inspect actual visual content. | A scan-only document and a visual-only stereotype fixture both receive appropriate processing; results distinguish OCR evidence from visual evidence. |
| FR-04 | Preserve sentence, paragraph, page/slide and document context, including long documents. | Full accepted inputs are processed through bounded chunks; no chunks are silently omitted or double-counted. |
| FR-05 | Run rules, NLP and LLM independently on shared source data, then reconcile their outputs. | Each layer has independently testable input/output; merged results retain provenance and disagreements. NLP relationships alone do not become bias verdicts. |
| FR-06 | Return evidence, category, explanation, recommendation, severity, confidence and contributing layer information for each concern. | Findings pass the shared schema and source validation; unassessed fields are labelled rather than fabricated. |
| FR-07 | Preview uploaded content and highlight the corresponding findings. | Clicking a finding navigates to its page/slide/cell/region; clicking a highlight opens its explanation. Overlapping findings remain accessible. |
| FR-08 | Show document-level findings without fabricating one exact offending word. | Representation findings link to all supporting examples and identify the scope of the pattern. |
| FR-09 | Show scan progress, completion, cancellation and partial failures. | An unavailable model, unreadable page or cancelled job never appears as a successful clean assessment. |
| FR-10 | Let educators accept, dismiss or annotate findings and edit suggested wording. | AI output remains distinguishable from educator decisions; the original upload is not overwritten. |
| FR-11 | Keep the application usable across repeated scans. | Starting a new scan cannot display stale findings on a different document; editing text invalidates its old highlights. |
| FR-12 | Expose analysis coverage and limitations. | Report processed pages, skipped content, image/OCR coverage, model/framework versions and incomplete stages. |

Document preview fidelity means readable content and accurate source navigation,
not pixel-identical rendering to Microsoft Office. Use the same rendering for
analysis coordinates and display. Excel findings must also retain sheet/cell
addresses if pagination changes in a later rendering.

## Out of scope unless subsequently agreed

- Cultural and Indigenous inclusivity, other languages, training an LLM from
  scratch, and sending teaching content to external AI providers.
- Automatically editing or overwriting the uploaded original.
- A guarantee that every possible bias is detected or that no false positives occur.

## Decisions still open

1. Approval of the exact image compatibility matrix and the Excel/multi-image
   counting rules above.
2. Deployment topology: one local educator/workstation is the proposed initial
   baseline. Shared university hosting, concurrency and authentication are not
   yet confirmed.
3. Persistent document/history storage, retention duration, login, and report
   export requirements. Proposed default: temporary local uploads, with explicit
   user action before saving history or exporting results.
4. Delivery deadline and frontend prototype. Neither has been supplied yet.

These open decisions do not prevent building the independent core engine, but
must be resolved before accepting the affected release features.
