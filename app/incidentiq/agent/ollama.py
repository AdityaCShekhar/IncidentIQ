from __future__ import annotations

import ipaddress
import json
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from incidentiq.agent.output import InvestigationReport
from incidentiq.agent.prompts import (
    SUMMARY_PROMPT_VERSION,
    compact_evidence,
    system_prompt,
    user_prompt,
)
from incidentiq.agent.validators import validate_evidence_references
from incidentiq.ingestion.schemas import EvidenceBundle

MAX_MODEL_CALLS = 2
MAX_RESPONSE_BYTES = 256 * 1024
MAX_COMPLETION_TOKENS = 384


@dataclass(frozen=True)
class ModelCallMetadata:
    requested_model: str
    returned_model: str
    created_at: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: float
    done_reason: str | None = None


class ModelOutputError(RuntimeError):
    """Keep bounded rejected output available to the offline benchmark."""

    def __init__(self, code: str, *, raw_output: str, metadata: dict):
        super().__init__(code)
        self.raw_output = raw_output
        self.metadata = metadata


@dataclass(frozen=True)
class ModelResult:
    report: InvestigationReport
    raw_output: str
    prompt_template_version: str
    model_calls: tuple[ModelCallMetadata, ...]
    generation_settings: dict

    @property
    def metadata(self) -> dict:
        return {
            "provider": "ollama",
            "requested_model": self.model_calls[0].requested_model,
            "returned_models": sorted({call.returned_model for call in self.model_calls}),
            "prompt_template_version": self.prompt_template_version,
            "generation_settings": self.generation_settings,
            "prompt_tokens": _sum_tokens(call.prompt_tokens for call in self.model_calls),
            "completion_tokens": _sum_tokens(call.completion_tokens for call in self.model_calls),
            "latency_ms": round(sum(call.duration_ms for call in self.model_calls), 3),
            "calls": [call.__dict__ for call in self.model_calls],
        }


def _sum_tokens(values) -> int | None:
    known = list(values)
    return sum(known) if known and all(value is not None for value in known) else None


def _validate_loopback_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Model endpoint must be a plain HTTP loopback URL")
    hostname = parsed.hostname.lower()
    try:
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError("Model endpoint must be a plain HTTP loopback URL") from None
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname == "localhost"
    if not loopback or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Model endpoint must be a plain HTTP loopback URL")
    return base_url.rstrip("/")


class OllamaAdapter:
    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 30,
        temperature: float = 0,
        seed: int = 17,
        num_ctx: int = 8192,
        max_completion_tokens: int = MAX_COMPLETION_TOKENS,
    ):
        if not model.strip() or len(model) > 200:
            raise ValueError("A bounded Ollama model name is required")
        if not 0 < timeout_seconds <= 120:
            raise ValueError("Model timeout must be between 0 and 120 seconds")
        if not 0 <= temperature <= 2 or not 1 <= num_ctx <= 32768:
            raise ValueError("Invalid local model generation settings")
        if (
            isinstance(max_completion_tokens, bool)
            or not isinstance(max_completion_tokens, int)
            or not 64 <= max_completion_tokens <= 4096
        ):
            raise ValueError("Completion token limit must be between 64 and 4096")
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**31 - 1:
            raise ValueError("Invalid local model generation settings")
        self.model = model.strip()
        self.base_url = _validate_loopback_url(base_url)
        self.timeout_seconds = timeout_seconds
        self.options = {
            "temperature": temperature,
            "seed": seed,
            "num_ctx": num_ctx,
            "num_predict": max_completion_tokens,
        }

    def generate_structured(
        self, messages: list[dict], schema: type, *, timeout_seconds: float | None = None
    ) -> tuple[str, ModelCallMetadata]:
        total_timeout = timeout_seconds or self.timeout_seconds
        deadline = time.perf_counter() + total_timeout
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": schema.model_json_schema(),
            "options": self.options,
        }
        started = time.perf_counter()
        try:
            with httpx.Client(
                timeout=httpx.Timeout(total_timeout),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    response.raise_for_status()
                    body_bytes = bytearray()
                    for block in response.iter_bytes():
                        if time.perf_counter() >= deadline:
                            raise RuntimeError("model_timeout")
                        body_bytes.extend(block)
                        if len(body_bytes) > MAX_RESPONSE_BYTES:
                            raise RuntimeError("model_response_too_large")
                    if time.perf_counter() >= deadline:
                        raise RuntimeError("model_timeout")
        except httpx.TimeoutException as error:
            raise RuntimeError("model_timeout") from error
        except httpx.HTTPError as error:
            raise RuntimeError("model_unavailable") from error
        elapsed_ms = (time.perf_counter() - started) * 1000
        try:
            body = json.loads(body_bytes)
            content = body["message"]["content"]
            returned_model = body["model"]
            created_at = body.get("created_at")
            if not isinstance(content, str) or not isinstance(returned_model, str):
                raise ValueError
            prompt_tokens = body.get("prompt_eval_count")
            completion_tokens = body.get("eval_count")
            if not isinstance(prompt_tokens, int):
                prompt_tokens = None
            if not isinstance(completion_tokens, int):
                completion_tokens = None
        except (ValueError, TypeError, KeyError):
            raise RuntimeError("invalid_model_response") from None
        return content, ModelCallMetadata(
            requested_model=self.model,
            returned_model=returned_model,
            created_at=created_at if isinstance(created_at, str) else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=elapsed_ms,
            done_reason=body.get("done_reason")
            if isinstance(body.get("done_reason"), str)
            else None,
        )

    def diagnose(
        self,
        bundle: EvidenceBundle,
        *,
        additional_context: str | None = None,
        prompt_template_version: str = SUMMARY_PROMPT_VERSION,
        max_calls: int = MAX_MODEL_CALLS,
        timeout_seconds: float | None = None,
    ) -> ModelResult:
        if not 1 <= max_calls <= MAX_MODEL_CALLS:
            raise ValueError("Invalid model call budget")
        compact_json, prompt_bundle = compact_evidence(bundle)
        messages = [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": user_prompt(compact_json, additional_context)},
        ]
        calls = []
        raw_output = ""
        validation_error: Exception | None = None
        deadline = time.monotonic() + (timeout_seconds or self.timeout_seconds)
        for attempt in range(max_calls):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("model_timeout") from validation_error
            raw_output, metadata = self.generate_structured(
                messages,
                InvestigationReport,
                timeout_seconds=min(self.timeout_seconds, remaining),
            )
            calls.append(metadata)
            try:
                report = InvestigationReport.model_validate_json(raw_output)
                validate_evidence_references(report, prompt_bundle)
                return ModelResult(
                    report=report,
                    raw_output=raw_output,
                    prompt_template_version=prompt_template_version,
                    model_calls=tuple(calls),
                    generation_settings=self.options.copy(),
                )
            except (ValidationError, ValueError) as error:
                validation_error = error
                if attempt + 1 < max_calls:
                    messages.extend(
                        [
                            {"role": "assistant", "content": raw_output},
                            {
                                "role": "user",
                                "content": (
                                    "The previous response failed schema or evidence-reference "
                                    "validation. Return a corrected structured report. Use only "
                                    "the IDs in the incident evidence. Do not add commentary."
                                ),
                            },
                        ]
                    )
        raise ModelOutputError(
            "invalid_model_output",
            raw_output=raw_output,
            metadata={
                "provider": "ollama",
                "requested_model": self.model,
                "prompt_template_version": prompt_template_version,
                "generation_settings": self.options.copy(),
                "prompt_tokens": _sum_tokens(call.prompt_tokens for call in calls),
                "completion_tokens": _sum_tokens(call.completion_tokens for call in calls),
                "calls": [call.__dict__ for call in calls],
            },
        ) from validation_error
