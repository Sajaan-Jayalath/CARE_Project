from backend.config import BackendSettings
from backend.database.database import Store, BackendError
from backend.services.file_service import extract_text
from backend.services.job_service import JobManager
import pytest


def test_restart_marks_active_jobs_interrupted_and_keeps_results(tmp_path):
    path = tmp_path / "care.sqlite3"
    store = Store(path)
    upload = store.add_document(extract_text("Example"), b"Example", 100)
    job = store.create_job(upload["upload_id"], 8)
    store.claim()
    store.update(job["job_id"], result={"status": "partial", "analysis": {"findings": [{"finding_id": "one"}]}})
    reopened = Store(path)
    reopened.recover()
    result = reopened.job(job["job_id"], include_result=True)
    assert result["status"] == "interrupted"
    assert result["error"]["code"] == "server_restarted"
    assert result["result"]["status"] == "partial"
    reopened.put_feedback(job["job_id"], "one", {"decision": "dismissed"})
    assert Store(path).feedback(job["job_id"])[0]["decision"] == "dismissed"


def test_cancellation_wins_race_with_completion(tmp_path):
    store = Store(tmp_path / "care.sqlite3")
    upload = store.add_document(extract_text("Example"), b"Example", 100)
    job = store.create_job(upload["upload_id"], 8)
    store.claim()
    store.cancel(job["job_id"])
    store.finish(job["job_id"], "completed", progress=100)
    assert store.job(job["job_id"])["status"] == "cancelled"


def test_single_supervisor_ownership_and_upload_cap(tmp_path):
    first = JobManager(BackendSettings(data_dir=tmp_path))
    first.start()
    try:
        second = JobManager(BackendSettings(data_dir=tmp_path))
        with pytest.raises(RuntimeError, match="Another CARE backend"):
            second.start()
        first.store.add_document(extract_text("Example"), b"Example", 1)
        with pytest.raises(BackendError) as error:
            first.store.add_document(extract_text("Other"), b"Other", 1)
        assert error.value.code == "storage_limit"
    finally:
        first.close()


def test_backend_keeps_no_data_between_sessions(tmp_path):
    manager = JobManager(BackendSettings(data_dir=tmp_path))
    manager.start()
    manager.store.add_document(extract_text("Example"), b"Example", 10)
    manager.close()
    assert not (tmp_path / "care.sqlite3").exists()
    restarted = JobManager(BackendSettings(data_dir=tmp_path))
    restarted.start()
    try:
        assert restarted.store.documents(50, 0) == []
    finally:
        restarted.close()
