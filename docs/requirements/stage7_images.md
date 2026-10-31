# Stage 7 — image analysis (OCR and visual review)

Implemented 2026-10-22. CARE now reads text inside images with local OCR and reviews
what images show with the local vision model. Everything runs on the workstation:
RapidOCR (ONNX, CPU; models ship with the package) and `qwen3.5:4b` through Ollama,
which has vision capability. No image leaves the computer.

## Workflow

```text
Upload (image, or PDF / Word / PowerPoint / Excel)
  → find images: uploads, embedded images, scanned PDF pages (no selectable text)
      skip icons (< 48 px), thin rules (ratio > 8:1) and exact repeats
      limits: 30 images per document, 25 M pixels per image, 500 M per document
  → for each image (first 40% of the progress bar):
      OCR  → paragraphs of text with line boxes → "ocr_text" sections
      VISION → observe people (presentation, role, activity) → assess the 6 criteria
      thumbnail stored for the results page
  → text pipeline (rules, NLP, LLM) over all text, OCR text included
  → reconciliation + document patterns + visual findings and visual patterns
```

## Files

| File | Role |
| --- | --- |
| `backend/extraction/image_extractor.py` | Validate image uploads; collect images with location and preview box |
| `backend/extraction/ocr.py` | RapidOCR; confidence filter; paragraph grouping with line boxes |
| `backend/ai/vision/image_context.py` | Vision prompt built from the framework's Visual Representation criteria |
| `backend/ai/vision/visual_representation.py` | Vision review, grounding checks, visual findings, cross-image patterns |
| `backend/services/analysis_service.py` | `image_stage`: OCR + vision before the text windows |
| `backend/services/preview_service.py` | Image uploads as preview pages; OCR line and image highlights |
| `GET /api/results/{job_id}/images/{image_id}` | Thumbnail of a reviewed image |

## Rules the visual review follows

- **No identity inference.** People are recorded as "presented as a woman/man" only when
  the visual presentation is clear, otherwise "unclear". Names are never used.
- **One person in a role is never a concern.** Single-image concerns need a visible gendered
  division, a portrayal of lower capability, sexualised imagery, explicit gender colour
  coding, or purposeless masculine-coded imagery.
- **Grounding.** Every concern must cite people the model listed; criteria about portrayal
  need at least one person whose presentation was clear. Ungrounded concerns are dropped
  and counted (`dropped_concerns`), never shown. Criterion names are matched to the
  framework ignoring case and punctuation.
- **Patterns across images:** at least 3 images and 80 % of clear appearances in one role
  family (technical, supporting, leadership) → a Medium, Low-confidence pattern finding.
- **Image text is untrusted.** Instructions written in an image are ignored.
- **Stylised text.** The vision model also copies the words it sees (`visible_text`). For short
  text (up to 40 words) whose words OCR missed, for example decorative lettering, that reading
  replaces the OCR reading for the text checks; long text keeps OCR's exact lines.
- **Single-image cases added 2026-10-26:** one gender left out while the other interacts as a
  group; one gender dismissing, scolding or belittling an upset person of another gender.

## Results

- Text findings from OCR carry evidence of type `ocr_text`; they highlight the OCR lines on
  the picture (PDF pages, slides, image uploads). Their sentences cannot be rewritten in
  the file, so **Accept** records the decision without a fix.
- Visual findings have `sources: ["Vision"]`, `scope` `image` or `document_pattern`,
  `criterion`, `images` (id, label, page/slide, box) and `observations`. Cards show the
  thumbnail and what the model saw; the preview outlines the image.
- `visual_analysis` in the result lists every image, each review (or its error), skipped
  images with reasons and OCR counts. Image warnings ("not inspected") are removed once
  the images were reviewed; skipped or failed images add explicit warnings and make the
  assessment partial.

## Verification

Automated: 12 image tests (intake per format, limits, real OCR, grouping, grounding,
patterns, pipeline, offline vision, warning resolution, preview, thumbnails) and a browser
test for image cards. Full suite: 252 Python tests, 9 browser tests.

Live check (2026-10-22, RTX 4050): a two-slide PowerPoint with a drawn office scene and a
worksheet image analysed in about 30 s. OCR read all text exactly; the text checks flagged
"his", "Hey guys" and "chairman"; the vision model identified a man at a computer and a
woman taking notes and raised a Visual Representation concern. Across two runs it chose
different criteria (Gendered Visual Authority, then Gendered Aesthetics) for the same
drawing: small-model judgements vary and require educator review.

## Not yet established

- **Accuracy.** No labelled image test set exists; the T16–T20 cases are text
  descriptions. Collect 20–50 real teaching images (concern and no-concern) before
  claiming performance.
- Vector drawings and charts without embedded raster images are not reviewed visually.
- Word and Excel images have no fixed page position, so they are shown as thumbnails
  rather than outlined in the preview.
- Legacy `.xls` images and SVG/HEIC uploads are not supported.
