# Stage 8 — local FastAPI application backend

Stage 7 (OCR/images/vision) is deliberately deferred. This backend runs the complete
text pathway from Stages 2–6 and supplies the API client for the later React UI.

## Start

Keep Ollama running with the configured downloaded model, then run from CARE:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

`http://127.0.0.1:8000/docs` is an offline endpoint reference, with the complete
OpenAPI contract at `/openapi.json`. Documentation does not load CDN scripts.
Use exactly one Uvicorn worker. An OS ownership lock prevents a second backend
process from operating on the same job database. Avoid `--reload` during long jobs;
a reload can interrupt an active job.

## Endpoints

| Method/path | Purpose |
| --- | --- |
| GET `/health` | Server/supervisor liveness, not model readiness. |
| GET `/api/models/status` | Check that Ollama responds and the configured model is installed locally. Does not guarantee enough inference memory. |
| POST `/api/uploads` | Multipart `file`; validate/extract and save the original and extracted source map. Returns 201 with `upload_id`. |
| POST `/api/uploads/text` | JSON `{"text":"..."}`; save pasted text. |
| GET `/api/uploads?limit=50&offset=0` | List saved uploads, newest first. |
| GET `/api/uploads/{upload_id}` | Retrieve extraction, preview text and source locations. |
| GET `/api/uploads/{upload_id}/original` | Download the original, served as an attachment. |
| DELETE `/api/uploads/{upload_id}` | Delete the upload and all related jobs/results/feedback; rejects active jobs with 409. |
| POST `/api/analysis` | JSON `{"upload_id":"..."}`; enqueue, return 202 with `job_id`. |
| POST `/api/analysis/text` | Convenience: save text and enqueue. |
| POST `/api/analysis/file` | Convenience: save multipart file and enqueue. |
| GET `/api/jobs?limit=50&offset=0` | List analysis jobs. |
| GET `/api/jobs/{job_id}` | Poll status, stage, progress, chunk counts, result availability and error. |
| POST `/api/jobs/{job_id}/cancel` | Request cancellation. Repeated cancellation is safe. A terminal job remains terminal. |
| GET `/api/results/{job_id}` | Return `{job,is_final,result,feedback}`; returns 409 before a checkpoint/result exists. |
| PUT `/api/feedback/{job_id}/{finding_id}` | Save/update educator feedback for a passage or document-pattern finding. |
| GET `/api/feedback/{job_id}` | Retrieve feedback. |
| POST `/api/fixes/{job_id}/{finding_id}/proposal` | The local model rewrites the sentences holding the finding's evidence. Falls back to the current wording with a message when the model is unavailable or returns unchanged text. |
| PUT `/api/fixes/{job_id}/{finding_id}` | Apply the educator-approved wording (`edits` with `base` = the wording proposed from). Records an accepted decision. Fixes on the same sentence merge; a stale proposal returns 409 `fix_outdated`. |
| DELETE `/api/fixes/{job_id}/{finding_id}` | Undo the fix and the accept decisions it recorded. |
| GET `/api/results/{job_id}/document` | A corrected copy of the upload with every applied fix written in (Word, PowerPoint, Excel XLSX/XLSM, PDF, text, Markdown, CSV/TSV; not legacy XLS). `X-CARE-Unapplied` counts fixes that could not be written. The original is never modified. |
| POST `/api/extract/text`, `/api/extract/file` | Existing extraction-only endpoints, without saving or inference. |

The two analysis convenience endpoints now return **jobs**, replacing their old
synchronous result response. Existing Python CLI analysis remains synchronous.
Prefer separate upload and analysis requests: if enqueueing a convenience request
fails because the queue is full, its saved upload is still available in the upload
list and can be submitted later.

Feedback payload:

```json
{"decision":"modified","comment":"Educator reviewed this passage.","recommendation":"Every programmer should test their code."}
```

Other decisions are `accepted` and `dismissed`; these omit `recommendation`.
Modified decisions require nonempty replacement wording. Feedback must reference
a finding in that job's final saved result. It never overwrites the AI explanation
or recommendation. Cancelled/failed jobs may still have reviewable saved partial
results; always show their job status and coverage alongside feedback.

## Background processing and progress

Uploads validate/extract before enqueueing. The persistent SQLite queue executes
one job at a time in a **spawned process**, keeping FastAPI responsive and avoiding
simultaneous CARE model jobs on the GPU. The worker calls the existing central
CARE pipeline, not a separate detector. It preserves rules/NLP/LLM outcomes,
reconciliation, source ranges and document-level patterns.

States: `queued`, `running`, `cancelling`, then `completed`, `partial`, `no_text`,
`failed`, `cancelled`, `timed_out`, or `interrupted`. The `is_final` flag means the
job stopped; it does not mean successful/complete analysis. `progress` is window
progress, not a calibrated time estimate. `stage` reports rules, NLP, LLM and
reconciliation while a window runs. A finished partial/no-text job can have 100%
job progress without having assessed every source item.

Completed windows produce durable, immutable result checkpoints. Large result
payloads go directly through SQLite transactions, not process pipes. This prevents
forced worker termination from leaving a half-written checkpoint message.
Results are available during processing with `is_final=false`. Cancellation,
timeout or crash retains the latest committed checkpoint. If the first window
never completed, there may be no result; this must not appear as a clean scan.

- Cancellation is immediate for queued jobs. Running jobs receive a cooperative
  signal; after a 3-second default grace period, the worker process is terminated.
  Its active inference window is unprocessed. Closing the HTTP client does not
  guarantee immediate GPU release inside Ollama; its server may finish/unwind the
  outstanding request independently. CARE does not stop the user's Ollama service.
- Whole-job timeout defaults to one hour, including worker startup. The existing
  per-Ollama-request timeout defaults to 300 seconds. Configure longer jobs when
  needed; no long-document latency guarantee is made.
- Missing/offline model, request timeout and recognized Ollama/Python memory
  errors produce explicit codes. Completed sibling layers remain available.
  Unexpected OS process death is `worker_exited`; the backend does not falsely
  claim it knows whether an unknown crash was caused by memory exhaustion.
- Restart marks previously running jobs `interrupted` and preserves checkpoints.
  Queued jobs resume. Interrupted jobs are not silently restarted: submit a new
  job for the saved upload. Graceful shutdown stops its active worker.
- A cancellation/completion race is resolved transactionally: an accepted running
  cancellation cannot silently become a normal completed result.

## Persistence, limits and local deployment

No history is kept (changed 2026-10-02). Uploads, extracted text, job state,
results/checkpoints and feedback live in a session-only SQLite working file in
the OS temp folder (`%TEMP%\care-backend`), outside the project. The backend
deletes it on every start and stop, so results and result links last only while
the backend is running. The file exists because the spawned analysis worker
writes checkpoints there for the server to read. Deletion is not a promise of
forensic erasure.

Defaults: 8 active/queued jobs total, 100 stored uploads, approximately 1 GB of
original-plus-extracted upload content. Results/feedback are additional disk use;
delete old uploads to reclaim application records. Upload limits from Stage 4
remain: 20 MB, 100 native/generated pages, supported text formats only. Storage
errors are reported separately from successful analysis. No third-party inference,
macro execution, OCR or visual analysis is added.

Configuration in `.env.example`:

| Variable | Default |
| --- | --- |
| `CARE_BACKEND_MAX_PENDING_JOBS` | `8` |
| `CARE_BACKEND_JOB_TIMEOUT_SECONDS` | `3600` |
| `CARE_BACKEND_CANCEL_GRACE_SECONDS` | `3` |
| `CARE_BACKEND_MAX_DOCUMENTS` | `100` |
| `CARE_BACKEND_DATA_DIR` | `<OS temp>/care-backend` (wiped on start/stop) |
| `CARE_BACKEND_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` |

Bind to loopback as shown above. This release is for one local educator/workstation,
not authenticated multi-user public hosting. Browser origin and host checks protect
the local interface; they are not user authentication. React development origins
are explicitly allowed, never `*`. No content is sent to cloud services.

## React integration

`frontend/src/services/api.js` now provides upload, submit, polling, cancellation,
results, feedback, preview/source retrieval and model-status functions. Set
`VITE_CARE_API_URL` if using another local backend port. Example component flow:

```javascript
import {uploadText, startAnalysis, waitForJob, getResults} from "./services/api";

const upload = await uploadText(text);
const started = await startAnalysis(upload.upload_id);
const job = await waitForJob(started.job_id, {signal, onProgress: setJob});
if (job.result_available) {
  const response = await getResults(job.job_id);
  // Display response.job.status and coverage, including partial/cancelled runs.
  // Passage findings: response.result.analysis?.findings
  // Document patterns: response.result.analysis?.document_analysis?.findings
}
```

AbortSignal stops browser polling only. Call `cancelJob(jobId)` to cancel server
work. HTTP/network errors expose actionable `CareApiError.code` values. The visual
React components and document viewer still await the user's frontend prototype;
this stage supplies and tests the API integration module, not the final website.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
node --test --test-isolation=none tests/frontend/api.test.mjs
```

Coverage includes real spawned-worker execution, queue limits, original upload
retrieval, cancellation, hard timeout, crashes, memory errors, checkpoint retention,
feedback validation, database reopening, restart recovery, cancellation races,
single-worker ownership, CORS/origin validation and frontend request/polling logic.
The complete AI/extraction test suite also runs. Mock worker cases test failure
handling, not model accuracy. Live verification is saved under ignored
`output/stage8-live-result.json`.

Verified completion: **212 Python tests and 3 frontend API-client tests passed**.
The live job completed with the locally installed `qwen3.5:4b`, returned one
finding, accepted feedback (HTTP 200), and restored both result and feedback after
closing and reopening the backend. No external inference service was used.
