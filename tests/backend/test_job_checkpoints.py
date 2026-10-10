from backend.services.analysis_service import analyse_extraction
from backend.services.file_service import extract_text
from backend.ai.llm.client import Settings, parse_completion, LLMError, OllamaClient
from tests.backend.test_text_pipeline import engine
import pytest
import httpx


def test_real_pipeline_checkpoints_have_stable_partial_coverage():
    snapshots = []
    result = analyse_extraction(extract_text("Every engineer should check his work. " * 60),
        engine=engine(), settings=Settings(), on_checkpoint=snapshots.append)
    assert len(snapshots) > 1
    first = snapshots[0]
    assert first["status"] == "partial"
    assert len(first["chunks"]) == 1
    assert first["analysis"]["layers"]["llm"]["skipped_section_ids"]
    assert result["status"] == "completed"


def test_ollama_memory_error_classified_for_json_and_http_errors():
    with pytest.raises(LLMError) as error:
        parse_completion({"error": "CUDA out of memory"}, "local")
    assert error.value.code == "insufficient_memory"
    def transport(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3.5:4b", "size": 100}]})
        return httpx.Response(500, json={"error": "model requires more system memory"})
    with pytest.raises(LLMError) as error:
        OllamaClient(Settings(), httpx.MockTransport(transport)).complete("Review text", "{}")
    assert error.value.code == "insufficient_memory"
