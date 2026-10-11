"""Session-only single-GPU queue with a supervised, cancellable spawned worker."""
import multiprocessing as mp
import os
import queue
import threading
import time

from backend.database.database import Store, BackendError


def analysis_worker(extraction_json, messages, cancel, store_path=None, job_id=None):
    """Spawn entry point: the web server never loads the NLP model itself."""
    from backend.extraction.common import ExtractionResult, ExtractionError
    from backend.services.analysis_service import analyse_extraction
    from backend.ai.llm.client import LLMError
    try:
        database = Store(store_path) if store_path else None
        def publish(kind, result):
            if database:
                # Large results go through an atomic SQLite transaction, not a
                # process pipe that could be left half-written by termination.
                database.update(job_id, result=result)
                errors = [r["error"] for c in result.get("chunks", []) for r in c.get("layers", {}).values() if r.get("error")]
                messages.put((kind, {"_persisted": True, "status": result["status"], "errors": errors}))
            else:
                messages.put((kind, result))
        def progress(index, total):
            messages.put(("progress", {"current_chunk": index, "total_chunks": total,
                                      "progress": int((index - 1) / total * 95), "stage": "analysing"}))
        result = analyse_extraction(ExtractionResult.model_validate(extraction_json),
            on_progress=progress, should_cancel=cancel.is_set,
            on_stage=lambda stage: messages.put(("progress", {"stage": stage})),
            on_checkpoint=lambda result: publish("checkpoint", result))
        publish("result", result)
    except MemoryError:
        messages.put(("error", {"code": "insufficient_memory", "message": "Analysis ran out of memory. Reduce document size or use a smaller model/context."}))
    except (ExtractionError, LLMError) as error:
        messages.put(("error", {"code": error.code, "message": str(error)}))
    except Exception:
        messages.put(("error", {"code": "analysis_failed", "message": "Analysis failed unexpectedly; completed checkpoints remain available."}))


class JobManager:
    def __init__(self, settings, worker=analysis_worker):
        self.settings, self.worker = settings, worker
        self.store = None  # Created in start(), once this process owns the data directory.
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.lock_file = None

    def start(self):
        # Reject multiple Uvicorn workers sharing the same local database/GPU.
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file = (self.settings.data_dir / "worker.lock").open("a+b")
        if os.fstat(self.lock_file.fileno()).st_size == 0:
            self.lock_file.write(b"0")
            self.lock_file.flush()
        self.lock_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            self.lock_file = None
            raise RuntimeError("Another CARE backend owns this data directory. Run exactly one Uvicorn worker.") from None
        # Nothing is kept between sessions: start from an empty working store.
        self._wipe()
        self.store = Store(self.settings.data_dir / "care.sqlite3")
        self.thread = threading.Thread(target=self._loop, name="care-job-supervisor", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=self.settings.cancel_grace_seconds + 10)
            if self.thread.is_alive():
                raise RuntimeError("CARE worker did not stop; its ownership lock remains held.")
        if self.lock_file:
            self._wipe()
            self.lock_file.close()
            self.lock_file = None

    def _wipe(self):
        """Delete the session's uploads, results and feedback (only while owning the lock)."""
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                (self.settings.data_dir / f"care.sqlite3{suffix}").unlink(missing_ok=True)
            except OSError:
                pass  # Still in use; the next start wipes it.

    def submit(self, upload_id):
        if self.stop.is_set() or not self.thread or not self.thread.is_alive():
            raise BackendError("worker_unavailable", "The job supervisor is unavailable. Restart the backend.", 503)
        result = self.store.create_job(upload_id, self.settings.max_pending_jobs)
        self.wake.set()
        return result

    def _loop(self):
        while not self.stop.is_set():
            identifier = self.store.claim()
            if identifier is None:
                self.wake.wait(0.2)
                self.wake.clear()
                continue
            try:
                self._run(identifier)
            except Exception:
                self.store.finish(identifier, "failed", error={"code": "worker_failed", "message": "Analysis worker failed; any saved checkpoint remains available."})

    def _run(self, identifier):
        job = self.store.job(identifier)
        extraction = self.store.document(job["upload_id"])["extraction"]
        context = mp.get_context("spawn")
        messages, cancelled = context.Queue(), context.Event()
        kwargs = {"store_path": self.store.path, "job_id": identifier} if self.worker is analysis_worker else {}
        process = context.Process(target=self.worker, args=(extraction, messages, cancelled), kwargs=kwargs, daemon=True)
        started, stopping, reason = time.monotonic(), None, None
        try:
            process.start()
            while True:
                now = time.monotonic()
                state = self.store.job(identifier)
                if reason is None:
                    if self.stop.is_set():
                        reason = "interrupted"
                    elif state["cancel_requested"]:
                        reason = "cancelled"
                    elif now - started >= self.settings.job_timeout_seconds:
                        reason = "timed_out"
                    if reason:
                        stopping = now
                        cancelled.set()
                        self.store.update(identifier, stage="stopping")
                if stopping is not None and now - stopping >= self.settings.cancel_grace_seconds:
                    self._finish_stopped(identifier, reason)
                    break
                try:
                    kind, payload = messages.get(timeout=0.1)
                except queue.Empty:
                    if not process.is_alive():
                        self._finish_stopped(identifier, reason or "failed", "worker_exited")
                        break
                    continue
                if kind == "progress" and not reason:
                    self.store.update(identifier, **payload)
                elif kind == "checkpoint":
                    if not payload.get("_persisted"):
                        self.store.update(identifier, result=payload)
                elif kind == "result":
                    # Check cancellation once more so a racing finish cannot
                    # silently turn an accepted cancellation into completion.
                    state = self.store.job(identifier)
                    reason = reason or ("cancelled" if state["cancel_requested"] else None)
                    if not payload.get("_persisted"):
                        self.store.update(identifier, result=payload)
                    if reason:
                        self._finish_stopped(identifier, reason)
                    else:
                        status = payload["status"]
                        if status not in {"completed", "partial", "no_text", "failed"}:
                            status = "failed"
                        errors = payload.get("errors") or [r["error"] for c in payload.get("chunks", []) for r in c.get("layers", {}).values() if r.get("error")]
                        self.store.finish(identifier, status, progress=100, error=errors[0] if errors else None)
                    break
                elif kind == "error":
                    self.store.finish(identifier, reason or "failed", error=payload)
                    break
        finally:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=3)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=3)
                process.close()
            messages.close()

    def _finish_stopped(self, identifier, reason, code=None):
        self.store.finish(identifier, reason,
            error={"code": code or reason, "message": {
                "cancelled": "Analysis cancelled. Only completed-window checkpoints are available.",
                "timed_out": "Analysis exceeded its job time limit. Completed-window checkpoints remain available.",
                "interrupted": "Backend stopped during analysis. Submit a new job to retry.",
                "failed": "Worker exited unexpectedly. This may be a process or memory failure; no successful scan is implied.",
            }[reason]})
