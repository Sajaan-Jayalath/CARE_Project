"""Local-only Ollama transport; no credentials, remote endpoint or cloud fallback."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import time
from typing import Literal

import httpx
from dotenv import load_dotenv
from pydantic import Field, ValidationError

from backend.ai.schemas.concern_schema import ModelAnalysis
from backend.ai.schemas.input_schema import Document, StrictModel


class LLMError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(StrictModel):
    base_url: Literal["http://127.0.0.1:11434"] = "http://127.0.0.1:11434"
    model: str = Field(default="qwen3.5:4b", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]*$")
    timeout_seconds: float = Field(default=300, gt=0, le=300)
    max_retries: int = Field(default=0, ge=0, le=5)
    max_output_tokens: int = Field(default=4096, ge=256, le=8192)
    context_tokens: int = Field(default=16384, ge=4096, le=32768)
    max_input_chars: int = Field(default=4000, ge=100, le=200000)
    thinking: bool = False

    @classmethod
    def from_env(cls):
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        fields = {"base_url": "BASE_URL", "model": "MODEL", "timeout_seconds": "TIMEOUT_SECONDS",
                  "max_retries": "MAX_RETRIES", "max_output_tokens": "MAX_OUTPUT_TOKENS",
                  "context_tokens": "CONTEXT_TOKENS", "max_input_chars": "MAX_INPUT_CHARS",
                  "thinking": "THINKING"}
        try:
            return cls(**{key: os.environ["CARE_LLM_" + suffix] for key, suffix in fields.items()
                          if "CARE_LLM_" + suffix in os.environ})
        except ValidationError:
            raise LLMError("configuration_error", "Invalid local LLM settings; see .env.example. Only http://127.0.0.1:11434 is allowed.") from None


@dataclass(frozen=True)
class Completion:
    analysis: ModelAnalysis
    model: str
    response_id: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


def parse_completion(body: dict, model: str) -> Completion:
    try:
        if body.get("error"):
            if any(term in str(body["error"]).casefold() for term in ("out of memory", "insufficient memory", "requires more system memory", "unable to allocate")):
                raise LLMError("insufficient_memory", "The local model ran out of memory. Use a smaller model/context or close memory-heavy applications.")
            raise LLMError("provider_error", "Ollama could not complete the analysis.")
        if body.get("done") is not True or body.get("done_reason") != "stop":
            raise LLMError("incomplete_response", "The local model did not finish. Try a smaller input or increase the output budget within the context limit.")
        message = body["message"]
        if message.get("role") != "assistant" or message.get("tool_calls"):
            raise LLMError("invalid_response", "The local model returned an unsupported response.")
        analysis = ModelAnalysis.model_validate_json(message["content"])
        usage = {}
        for source, target in (("prompt_eval_count", "input_tokens"), ("eval_count", "output_tokens")):
            value = body.get(source)
            if type(value) is int and value >= 0:
                usage[target] = value
        if len(usage) == 2:
            usage["total_tokens"] = sum(usage.values())
        return Completion(analysis, body.get("model") or model, usage=usage)
    except (ValidationError, ValueError, TypeError, AttributeError, KeyError):
        raise LLMError("invalid_response", "The local model returned malformed or invalid CARE JSON.") from None


class OllamaClient:
    provider = "ollama-local"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def complete(self, system_prompt: str, document_json: str, category: str | None = None,
                 grounded_sections: bool = False) -> Completion:
        schema = ModelAnalysis.model_json_schema()
        if category is not None:
            schema["$defs"]["Category"]["enum"] = [category]
        # Keep teaching text out of the system message, including repair enums.
        schema_instructions = json.dumps(schema, separators=(",", ":"))
        if grounded_sections:
            # Constrained regeneration can select a whole supplied section as
            # evidence, but cannot paraphrase it or attach it to another section.
            document = Document.model_validate_json(document_json)
            schema["$defs"]["EvidenceReference"] = {"anyOf": [
                {"type": "object", "properties": {
                    "section_id": {"const": section.section_id},
                    "quote": {"const": section.text}, "occurrence": {"const": 1}},
                 "required": ["section_id", "quote", "occurrence"], "additionalProperties": False}
                for section in document.sections]}
        system_prompt += "\nReturn only JSON matching this schema:\n" + schema_instructions
        # A conservative byte bound for the selected Qwen byte-level tokenizer,
        # with additional room for the chat template and the entire output.
        budget = len((system_prompt + document_json).encode("utf-8")) + 512 + self.settings.max_output_tokens
        if budget > self.settings.context_tokens:
            raise LLMError("input_too_large", "The framework, input and output reserve exceed the local context budget. Supply a shorter passage; no text was truncated.")
        payload = {
            "model": self.settings.model,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": document_json}],
            "stream": False, "think": self.settings.thinking, "format": schema,
            "options": {"temperature": 0.6 if self.settings.thinking else 0.2,
                        "top_p": 0.95 if self.settings.thinking else 0.8, "top_k": 20,
                        # Exact evidence extraction must not penalise repeating
                        # source words. Lower sampling variance helps labels.
                        "min_p": 0.0, "presence_penalty": 0.0,
                        "repeat_penalty": 1.0,
                        "seed": 42, "num_ctx": self.settings.context_tokens,
                        "num_predict": self.settings.max_output_tokens},
        }
        # Ignore proxy environment variables; never follow redirects off-device.
        with httpx.Client(timeout=self.settings.timeout_seconds, transport=self.transport,
                          follow_redirects=False, trust_env=False) as client:
            try:
                installed = client.get(self.settings.base_url + "/api/tags")
                if not installed.is_success:
                    raise LLMError("provider_error", "Could not verify locally installed Ollama models.")
                models = installed.json()["models"]
                local = next((m for m in models if m.get("name") == self.settings.model), None)
                if local is None:
                    raise LLMError("provider_model_unavailable", "Model is not installed. Run ollama list and download the configured local model first.")
                if local.get("remote_host") or local.get("remote_model") or "cloud" in self.settings.model.lower() or not local.get("size", 0):
                    raise LLMError("configuration_error", "CARE requires downloaded local model weights; cloud models are disabled.")
                for attempt in range(self.settings.max_retries + 1):
                    response = client.post(self.settings.base_url + "/api/chat", json=payload)
                    if response.status_code in (500, 502, 503, 504) and attempt < self.settings.max_retries and "retry-after" not in response.headers:
                        time.sleep(min(2 ** attempt, 8))
                        continue
                    if not response.is_success:
                        code = "provider_model_unavailable" if response.status_code == 404 else "provider_error"
                        # Classify local resource errors without exposing raw
                        # provider messages, paths or submitted content.
                        message = response.text[:8192].casefold()
                        if any(term in message for term in ("out of memory", "insufficient memory", "requires more system memory", "unable to allocate", "cuda error: out of memory")):
                            raise LLMError("insufficient_memory", "The local model ran out of memory. Close memory-heavy applications or use a smaller local model/context.")
                        raise LLMError(code, f"Local Ollama returned HTTP {response.status_code}. Check that the model is installed and enough memory is available.")
                    completion = parse_completion(response.json(), self.settings.model)
                    if category is not None and any(c.category.value != category for c in completion.analysis.concerns):
                        raise LLMError("invalid_response", "The local model returned a category outside the requested review pass.")
                    return completion
            except httpx.TimeoutException:
                raise LLMError("provider_timeout", "Local inference timed out. Try a smaller input and close GPU-heavy applications.") from None
            except httpx.RequestError:
                raise LLMError("provider_connection", "Cannot reach local Ollama. Start the Ollama app and try again.") from None
            except (ValueError, TypeError, KeyError, AttributeError):
                raise LLMError("invalid_response", "Ollama returned an invalid response.") from None
        raise LLMError("provider_error", "Local analysis did not complete.")
