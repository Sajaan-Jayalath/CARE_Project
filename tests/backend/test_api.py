import time
import pytest
from fastapi.testclient import TestClient
from backend.config import BackendSettings
from backend.main import create_app
from tests.backend.job_fixtures import fake_worker


@pytest.fixture
def client(tmp_path):
    app = create_app(BackendSettings(data_dir=tmp_path, cancel_grace_seconds=0.1), worker=fake_worker)
    with TestClient(app) as client:
        yield client


def wait_job(client, job_id, predicate=lambda j: j["is_final"]):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if predicate(job):
            return job
        time.sleep(0.05)
    pytest.fail("Job did not reach expected state")


def submit(client, text="Teaching text"):
    response = client.post("/api/analysis/text", json={"text": text})
    assert response.status_code == 202, response.text
    return response.json()


def test_extraction_api_text_file_and_validation(client):
    assert client.get("/health").json()["scope"] == "text-only"
    text = "Every programmer should test his code."
    result = client.post("/api/extract/text", json={"text": text})
    assert result.status_code == 200
    assert result.json()["document"]["sections"][0]["text"] == text
    upload = client.post("/api/extract/file", files={"file": ("lesson.txt", text.encode(), "text/plain")})
    assert upload.status_code == 200
    bad = client.post("/api/uploads", files={"file": ("lesson.png", b"fake", "image/png")})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "unsupported_format"
    assert client.post("/api/extract/text", json={"text": "  "}).status_code == 422


def test_upload_job_progress_results_feedback_and_delete(client):
    upload = client.post("/api/uploads", files={"file": ("lesson.txt", b"Teaching text")})
    assert upload.status_code == 201
    uid = upload.json()["upload_id"]
    assert client.get(f"/api/uploads/{uid}/original").content == b"Teaching text"
    started = client.post("/api/analysis", json={"upload_id": uid})
    assert started.status_code == 202
    job_id = started.json()["job_id"]
    job = wait_job(client, job_id)
    assert job["status"] == "completed" and job["progress"] == 100
    result = client.get(f"/api/results/{job_id}").json()
    assert result["is_final"] and result["result"]["analysis"]["findings"]
    endpoint = f"/api/feedback/{job_id}/local-1"
    assert client.put(endpoint, json={"decision": "modified"}).status_code == 422
    changed = client.put(endpoint, json={"decision": "modified", "recommendation": "Educator wording", "comment": "Reviewed"})
    assert changed.status_code == 200
    assert client.put(f"/api/feedback/{job_id}/pattern-1", json={"decision": "dismissed"}).status_code == 200
    assert client.put(f"/api/feedback/{job_id}/unknown", json={"decision": "accepted"}).status_code == 404
    stored = client.get(f"/api/results/{job_id}").json()
    assert stored["result"]["analysis"]["findings"][0]["recommendation"] == "Original recommendation"
    assert len(stored["feedback"]) == 2
    assert client.delete(f"/api/uploads/{uid}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_active_cancellation_keeps_checkpoint_and_queue_moves_on(client):
    job = submit(client, "wait")
    ready = wait_job(client, job["job_id"], lambda j: bool(j["result_available"]))
    assert not ready["is_final"]
    assert client.delete(f"/api/uploads/{job['upload_id']}").status_code == 409
    assert client.put(f"/api/feedback/{job['job_id']}/local-1", json={"decision": "accepted"}).status_code == 409
    duplicate = client.post("/api/analysis", json={"upload_id": job["upload_id"]})
    assert duplicate.status_code == 409
    assert client.post(f"/api/jobs/{job['job_id']}/cancel").status_code == 202
    final = wait_job(client, job["job_id"])
    assert final["status"] == "cancelled" and final["progress"] < 100
    result = client.get(f"/api/results/{job['job_id']}").json()
    assert result["is_final"] and result["result"]["status"] == "partial"
    next_job = submit(client)
    assert wait_job(client, next_job["job_id"])["status"] == "completed"


def test_queued_cancellation_and_result_not_ready(client):
    first = submit(client, "wait")
    wait_job(client, first["job_id"], lambda j: j["result_available"])
    second = submit(client)
    assert client.get(f"/api/results/{second['job_id']}").status_code == 409
    result = client.post(f"/api/jobs/{second['job_id']}/cancel").json()
    assert result["status"] == "cancelled"
    assert client.post(f"/api/jobs/{second['job_id']}/cancel").json()["status"] == "cancelled"
    client.post(f"/api/jobs/{first['job_id']}/cancel")


@pytest.mark.parametrize("text,status,code", [("memory", "failed", "insufficient_memory"), ("crash", "failed", "worker_exited"), ("partial", "partial", "provider_model_unavailable")])
def test_worker_errors_preserve_partial_result(client, text, status, code):
    job = submit(client, text)
    final = wait_job(client, job["job_id"])
    assert final["status"] == status and final["error"]["code"] == code
    assert final["result_available"]


def test_job_timeout_is_enforced_by_supervisor(tmp_path):
    with TestClient(create_app(BackendSettings(data_dir=tmp_path, job_timeout_seconds=2, cancel_grace_seconds=0), worker=fake_worker)) as client:
        job = submit(client, "wait")
        final = wait_job(client, job["job_id"])
        assert final["status"] == "timed_out" and final["error"]["code"] == "timed_out"


def test_origin_validation_offline_docs_and_missing_ids(client):
    assert client.get("/api/uploads", headers={"Origin": "https://untrusted.example"}).status_code == 403
    allowed = client.options("/api/uploads", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert client.get("/api/jobs/missing").status_code == 404
    assert client.get("/api/results/missing").status_code == 404
    assert "cdn" not in client.get("/docs").text
    assert "/api/analysis" in client.get("/openapi.json").json()["paths"]


def test_oversized_multipart_is_rejected(client):
    result = client.post("/api/uploads", files={"file": ("large.txt", b"x" * 21_000_001)})
    assert result.status_code == 413


def test_queue_capacity_returns_429(tmp_path):
    with TestClient(create_app(BackendSettings(data_dir=tmp_path, max_pending_jobs=1, cancel_grace_seconds=0), worker=fake_worker)) as client:
        job = submit(client, "wait")
        wait_job(client, job["job_id"], lambda j: j["result_available"])
        assert client.post("/api/analysis/text", json={"text": "Another upload"}).status_code == 429
