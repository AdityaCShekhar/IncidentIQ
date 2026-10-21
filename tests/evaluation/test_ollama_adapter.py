"""Adapter protocol checks using synthetic responses, not model-quality evidence."""

from dataclasses import asdict
from pathlib import Path

import httpx
import pytest
from incidentiq.agent.ollama import ModelOutputError, OllamaAdapter
from incidentiq.baselines.rules import run_rules
from incidentiq.evaluation.benchmark import evaluate
from incidentiq.evaluation.dataset import load_dataset

CASE = load_dataset(Path(__file__).resolve().parents[2] / "evals", split="dev")[0]


def mock_server(monkeypatch, content, done_reason="stop"):
    requests = []

    def respond(request):
        import json

        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "message": {"content": content},
                "prompt_eval_count": 100,
                "eval_count": 384,
                "done_reason": done_reason,
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        "incidentiq.agent.ollama.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    return requests


def test_native_protocol_schema_and_configurable_generation_budget(monkeypatch):
    requests = mock_server(monkeypatch, run_rules(CASE.bundle).model_dump_json())
    result = OllamaAdapter("test-model", max_completion_tokens=1536).diagnose(CASE.bundle)
    assert requests[0]["options"]["num_predict"] == 1536
    assert requests[0]["format"]["type"] == "object"
    assert requests[0]["stream"] is False
    assert result.report == run_rules(CASE.bundle)
    assert asdict(result.model_calls[0])["done_reason"] == "stop"


def test_truncated_output_is_retained_with_repair_usage_and_stop_reason(monkeypatch):
    raw = '{"incident_summary":"unfinished'
    requests = mock_server(monkeypatch, raw, done_reason="length")
    adapter = OllamaAdapter("test-model")
    with pytest.raises(ModelOutputError) as error:
        adapter.diagnose(CASE.bundle)
    assert len(requests) == 2
    assert error.value.raw_output == raw
    assert error.value.metadata["completion_tokens"] == 768
    assert error.value.metadata["prompt_tokens"] == 200
    assert all(call["done_reason"] == "length" for call in error.value.metadata["calls"])
    metadata, rows = evaluate([CASE], strategy=adapter.diagnose)
    assert rows[0]["status"] == "failed"
    assert rows[0]["error_code"] == "invalid_model_output"
    assert rows[0]["raw_output"] == raw
    assert metadata["metrics"]["completion_tokens"] == 768


@pytest.mark.parametrize("limit", [True, 0, 63, 4097, 1.5])
def test_generation_budget_is_bounded(limit):
    with pytest.raises(ValueError, match="Completion token limit"):
        OllamaAdapter("test-model", max_completion_tokens=limit)
