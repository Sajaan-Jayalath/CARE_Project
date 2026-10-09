// Shared React integration point. No cloud inference and no framework dependency.
const base = (import.meta.env?.VITE_CARE_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export class CareApiError extends Error {
  constructor(message, code, status) {
    super(message);
    this.name = "CareApiError";
    this.code = code;
    this.status = status;
  }
}

async function request(path, { body, signal, method = "GET" } = {}) {
  const form = typeof FormData !== "undefined" && body instanceof FormData;
  let response;
  try {
    response = await fetch(`${base}${path}`, {
      method, signal,
      headers: body && !form ? { "Content-Type": "application/json" } : {},
      body: body ? (form ? body : JSON.stringify(body)) : undefined,
    });
  } catch (error) {
    if (signal?.aborted || error.name === "AbortError") throw error;
    throw new CareApiError("Cannot reach the local CARE backend. Start FastAPI and try again.", "backend_unreachable", 0);
  }
  if (response.status === 204) return null;
  let data;
  try { data = await response.json(); }
  catch { throw new CareApiError("The backend returned an unreadable response.", "invalid_response", response.status); }
  if (!response.ok) {
    throw new CareApiError(data.error?.message || "CARE request failed. Check the input and backend status.",
      data.error?.code || "request_failed", response.status);
  }
  return data;
}

const id = encodeURIComponent;
export const uploadText = (text, signal) => request("/api/uploads/text", { method: "POST", body: { text }, signal });
export function uploadFile(file, signal) {
  const body = new FormData();
  body.append("file", file);
  return request("/api/uploads", { method: "POST", body, signal });
}
export const getUpload = (uploadId, signal) => request(`/api/uploads/${id(uploadId)}`, { signal });
export const originalUrl = uploadId => `${base}/api/uploads/${id(uploadId)}/original`;
export const deleteUpload = uploadId => request(`/api/uploads/${id(uploadId)}`, { method: "DELETE" });
export const startAnalysis = (uploadId, signal) => request("/api/analysis", { method: "POST", body: { upload_id: uploadId }, signal });
export const getJob = (jobId, signal) => request(`/api/jobs/${id(jobId)}`, { signal });
export const cancelJob = jobId => request(`/api/jobs/${id(jobId)}/cancel`, { method: "POST" });
export const getResults = (jobId, signal) => request(`/api/results/${id(jobId)}`, { signal });
export const getFeedback = jobId => request(`/api/feedback/${id(jobId)}`);
export const saveFeedback = (jobId, findingId, feedback) => request(`/api/feedback/${id(jobId)}/${id(findingId)}`, { method: "PUT", body: feedback });
export const modelStatus = () => request("/api/models/status");
export const listJobs = signal => request('/api/jobs?limit=5', { signal });
export const getDocumentPreview = (jobId, signal) => request(`/api/results/${id(jobId)}/preview`, { signal });
export const previewPageUrl = (uploadId, page) => `${base}/api/uploads/${id(uploadId)}/preview/pages/${page}`;

export async function waitForJob(jobId, { signal, onProgress = () => {}, intervalMs = 1000 } = {}) {
  while (true) {
    signal?.throwIfAborted();
    const job = await getJob(jobId, signal);
    onProgress(job);
    if (job.is_final) return job; // cancelled/failed/timed_out are not successes
    await new Promise((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(signal.reason || new DOMException("Aborted", "AbortError")); };
      const timer = setTimeout(() => { signal?.removeEventListener("abort", abort); resolve(); }, intervalMs);
      signal?.addEventListener("abort", abort, { once: true });
      if (signal?.aborted) abort();
    });
  }
}
// Aborting polling only stops the browser wait. Call cancelJob to cancel the server job.
