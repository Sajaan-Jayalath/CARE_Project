"""Picklable spawned workers for testing process supervision without a real LLM."""
import os
import time


def fake_worker(extraction, messages, cancel):
    text = extraction["document"]["sections"][0]["text"]
    result = {"status": "completed", "extraction": extraction, "analysis": {
        "findings": [{"finding_id": "local-1", "explanation": "Original AI explanation", "recommendation": "Original recommendation"}],
        "document_analysis": {"findings": [{"finding_id": "pattern-1"}]}}, "chunks": []}
    messages.put(("progress", {"stage": "fixture_running", "current_chunk": 1, "total_chunks": 2, "progress": 45}))
    checkpoint = {**result, "status": "partial"}
    messages.put(("checkpoint", checkpoint))
    if text == "memory":
        messages.put(("error", {"code": "insufficient_memory", "message": "Insufficient memory."}))
    elif text == "crash":
        messages.close()
        messages.join_thread()
        os._exit(7)
    elif text == "wait":
        time.sleep(120)
    elif text == "cooperate":
        while not cancel.wait(0.02):
            pass
        messages.put(("result", {**checkpoint, "cancelled": True}))
    elif text == "partial":
        messages.put(("result", {**checkpoint, "chunks": [{"layers": {"llm": {"error": {"code": "provider_model_unavailable", "message": "Model not installed."}}}}]}))
    else:
        messages.put(("result", result))
