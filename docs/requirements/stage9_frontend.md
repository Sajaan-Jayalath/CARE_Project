# Stage 9 — CARE web interface

The React application implements the supplied blue-and-white prototype with three
pages: Home, Analysis Results and About. Contact links are omitted. It uses the
existing FastAPI job system and local Ollama inference; no cloud AI service or
remote fonts/assets are required at runtime.

## Start locally

From the repository root, start the backend in one PowerShell terminal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open **http://127.0.0.1:5173**. Keep Ollama running with the configured local model.
`npm run build` creates the production bundle. `npm run preview` serves that bundle
on the same local port for verification. FastAPI remains a separate process.
To change the API address, set `VITE_CARE_API_URL` in `frontend/.env.local` and restart
Vite/rebuild. Any changed frontend origin must also be allowed by backend CORS.

## Implemented flows

- Upload PDF, DOCX, PPTX, XLSX/XLSM/XLS, TXT, Markdown, CSV or TSV, or paste text.
  The backend enforces document limits; the browser also checks extensions and
  20 MB file size. The 100-page policy uses generated text pages for formats
  without fixed pagination, as documented in Stage 4.
- Submit a job, poll progress, cancel, retry a failed connection, or rerun an upload.
  Recent jobs on Home and bookmarkable result URLs let users return to saved work.
- Show passage totals, severity and category filters, recommendations, confidence,
  contributing assessments, disagreements and document-wide patterns. Document
  patterns remain separate from passage totals to avoid misleading double counts.
- Preview **original PDF pages** and locally rendered Word, PowerPoint and Excel
  pages, including their formatting, tables and images. Page selection and zoom
  are available. High concerns are red, Medium amber, Low green, and unassessed gray.
  Clicking a highlight opens its finding; overlapping concerns use the highest
  severity color and remain individually selectable.
- Office previews require local LibreOffice (installed on this development machine).
  Other machines can install it and optionally set `CARE_LIBREOFFICE_PATH` to the
  executable. Office pagination/fonts may differ from Microsoft Office. Conversion
  is bounded to 90 seconds, 100 rendered pages and a 200 MB PDF, with cached previews
  removed when their upload is deleted. Originals remain unchanged.
- A **Text view** preserves accessible source text and page/slide/paragraph/sheet/cell
  labels. Plain text, Markdown, CSV/TSV and pasted input use this view directly.
  Evidence that cannot be mapped uniquely to rendered pages is explicitly reported,
  with a link to text view. Original documents can also be downloaded.
- Canonical analysis offsets map back to original extracted sections. Unicode
  code-point offsets are respected. Unverifiable quotes are not highlighted.
- **Accept applies a fix** (added 2026-10-22). The local model rewrites the sentence(s)
  holding the concern; the educator can edit the wording before **Apply fix**. The
  preview then shows the corrected document: Office files and PDFs are re-rendered from
  the corrected copy (a few seconds; cached until the fixes change) and text inputs update
  immediately. Fixed passages are highlighted in teal on their new wording, other
  highlights move with the text, and the card shows the new wording with **Undo fix**.
  Dismiss and Modify / add note work as before. If the model is unavailable or returns
  unchanged wording, the educator types the correction.
- **Download document** saves a corrected copy of the upload with every applied fix
  (Word, PowerPoint, Excel, PDF, text, Markdown, CSV/TSV). Formatting runs are kept;
  in PDFs each edited text block is redrawn in place, so its font may differ and long
  replacements shrink to fit. Legacy `.xls` cannot be exported. The original upload is
  never modified. This replaces the earlier JSON report download.
- Show partial, cancelled, failed and no-text states explicitly, including extraction
  warnings and per-layer coverage. A missing finding is not a claim of no bias.
- Keyboard navigation, labelled controls, focus indicators, status/error announcements,
  reduced-motion support and layouts for mobile and desktop are included.

Images can be displayed as part of document rendering, but OCR and actual visual
analysis remain deferred to Stage 7. The framework's
Visual Representation category can still describe supplied text; it does not mean
the application has inspected images.

## Verification

```powershell
cd frontend
npm test
npm run build
```

Browser tests use installed Microsoft Edge. They cover navigation, uploads, text
submission, evidence/Unicode mapping, overlapping categories, partial results,
cancellation, feedback, JSON downloads, mobile overflow and connection failures.
The live test is opt-in and requires the backend and Ollama:

```powershell
$env:CARE_LIVE_TEST = '1'
npm test
Remove-Item Env:CARE_LIVE_TEST
```

The live test runs a real local analysis, saves educator feedback, refreshes the page
to verify persistence, then deletes its own test upload. It does not establish model
detection accuracy. Screenshots and browser artifacts are kept under ignored `output/`.
