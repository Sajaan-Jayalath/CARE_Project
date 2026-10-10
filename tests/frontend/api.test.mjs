// Run with: node --test tests/frontend/api.test.mjs
import { readFile } from "node:fs/promises";
import assert from "node:assert/strict";
import { test } from "node:test";
const source = await readFile(new URL("../../frontend/src/services/api.js", import.meta.url), "utf8");
const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

test("upload, submit, poll and retrieve use the backend contract", async () => {
  const calls = [];
  const responses = [{ upload_id: "abc" }, { job_id: "job" }, { status: "running", is_final: false }, { status: "partial", is_final: true }, { result: { status: "partial" } }];
  globalThis.fetch = async (url, options) => {
    calls.push([url, options]);
    return { ok: true, status: 200, json: async () => responses.shift() };
  };
  const upload = await api.uploadText("Teaching text");
  const job = await api.startAnalysis(upload.upload_id);
  const progress = [];
  const final = await api.waitForJob(job.job_id, { intervalMs: 1, onProgress: value => progress.push(value.status) });
  assert.equal(final.status, "partial");
  await api.getResults(job.job_id);
  assert.deepEqual(progress, ["running", "partial"]);
  assert.equal(JSON.parse(calls[1][1].body).upload_id, "abc");
  assert.ok(calls[4][0].endsWith("/api/results/job"));
});

test("backend errors and polling cancellation are explicit", async () => {
  globalThis.fetch = async () => ({ ok: false, status: 429, json: async () => ({ error: { code: "queue_full", message: "Queue full" } }) });
  await assert.rejects(api.startAnalysis("id"), error => error.code === "queue_full" && error.status === 429);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(api.waitForJob("id", { signal: controller.signal }), error => error.name === "AbortError");
});

test("unavailable backend produces an actionable client error", async () => {
  globalThis.fetch = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(api.modelStatus(), error => error.code === "backend_unreachable");
});
