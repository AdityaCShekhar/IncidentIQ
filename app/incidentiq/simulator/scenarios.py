import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import UUID, uuid4

import httpx

from incidentiq.simulator.deployments import DeploymentResult


def now() -> str:
    return datetime.now(UTC).isoformat()


def run_slow_dependency(
    client: httpx.Client,
    *,
    requests: int = 5,
    baseline_ms: int = 20,
    fault_ms: int = 500,
) -> dict:
    if not 1 <= requests <= 20:
        raise ValueError("requests must be between 1 and 20")
    if not 0 <= baseline_ms < fault_ms <= 2000:
        raise ValueError("Require 0 <= baseline_ms < fault_ms <= 2000")
    result = {
        "schema_version": 1,
        "scenario": "slow_dependency",
        "execution": "http",
        "configuration": {
            "requests_per_phase": requests,
            "baseline_ms": baseline_ms,
            "fault_ms": fault_ms,
        },
        "started_at": now(),
        "phases": [],
    }
    for phase_name, delay_ms in (("baseline", baseline_ms), ("fault", fault_ms)):
        run_id = str(uuid4())
        samples = []
        started_at = now()
        for _ in range(requests):
            request_id = str(uuid4())
            started = perf_counter()
            response = client.post(
                "/checkout",
                json={"run_id": run_id, "delay_ms": delay_ms},
                headers={"X-Request-ID": request_id},
            )
            elapsed_ms = (perf_counter() - started) * 1000
            response.raise_for_status()
            trace_id = response.headers.get("X-Trace-ID", "")
            if len(trace_id) != 32 or UUID(hex=trace_id).int == 0:
                raise ValueError("Missing or invalid trace ID from checkout")
            if response.headers.get("X-Request-ID") != request_id:
                raise ValueError("Checkout did not preserve request ID")
            body = response.json()
            if body.get("status") != "completed" or body.get("run_id") != run_id:
                raise ValueError("Unexpected checkout result")
            samples.append(
                {
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "status_code": response.status_code,
                    "latency_ms": round(elapsed_ms, 3),
                }
            )
        result["phases"].append(
            {
                "name": phase_name,
                "run_id": run_id,
                "started_at": started_at,
                "ended_at": now(),
                "median_latency_ms": median(sample["latency_ms"] for sample in samples),
                "samples": samples,
            }
        )
    baseline, fault = result["phases"]
    result["ended_at"] = now()
    result["observed_median_increase_ms"] = round(
        fault["median_latency_ms"] - baseline["median_latency_ms"], 3
    )
    result["latency_increased"] = result["observed_median_increase_ms"] > 0
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a reproducible synthetic fault against checkout"
    )
    parser.add_argument(
        "name",
        choices=["slow_dependency", "deployment_regression", "pool_exhaustion", "retry_storm"],
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--requests", type=int, default=5)
    parser.add_argument("--baseline-ms", type=int, default=20)
    parser.add_argument("--fault-ms", type=int, default=500)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path("docs/demos") / f"{args.name.replace('_', '-')}.json"
    try:
        with httpx.Client(base_url=args.base_url, timeout=5, trust_env=False) as client:
            if args.name == "deployment_regression":
                result = run_deployment_regression(client, requests=args.requests)
            elif args.name == "pool_exhaustion":
                result = run_pool_exhaustion(client, requests=max(4, args.requests))
            elif args.name == "retry_storm":
                result = run_retry_storm(client, requests=args.requests)
            else:
                result = run_slow_dependency(
                    client,
                    requests=args.requests,
                    baseline_ms=args.baseline_ms,
                    fault_ms=args.fault_ms,
                )
    except (httpx.HTTPError, ValueError) as error:
        parser.exit(
            1, f"Scenario failed ({type(error).__name__}). Check checkout and inventory logs.\n"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Saved measured HTTP results to {output}")
    for phase in result.get("phases", []):
        if "median_latency_ms" in phase:
            print(f"{phase['name']}: median {phase['median_latency_ms']:.3f} ms")
        if phase.get("samples"):
            print(f"  trace: {phase['samples'][-1]['trace_id']}")
        if "error_rate" in phase:
            print(f"  observed error rate: {phase['error_rate']:.0%}")
    if args.name == "deployment_regression":
        return 0 if result["regression_observed"] else 2
    if args.name == "pool_exhaustion":
        return 0 if result["pool_exhaustion_observed"] else 2
    if args.name == "retry_storm":
        return 0 if result["retry_storm_observed"] else 2
    print(f"Observed median increase: {result['observed_median_increase_ms']:.3f} ms")
    return 0 if result["latency_increased"] else 2


def run_deployment_regression(client: httpx.Client, *, requests: int = 5) -> dict:
    """Measure baseline, activate a revision, then roll back and measure recovery."""
    if not 1 <= requests <= 20:
        raise ValueError("requests must be between 1 and 20")
    run_id = str(uuid4())
    result = {
        "schema_version": 1,
        "scenario": "deployment_regression",
        "execution": "http",
        "configuration": {"requests_per_phase": requests, "delay_ms": 20},
        "started_at": now(),
        "phases": [],
    }

    def activate(revision):
        response = client.post(
            "/simulator/deployments", json={"run_id": run_id, "revision": revision}
        )
        response.raise_for_status()
        deployed = DeploymentResult.model_validate_json(response.content)
        if str(deployed.run_id) != run_id or deployed.revision != revision:
            raise ValueError("Unexpected deployment acknowledgement")
        return deployed.model_dump(mode="json")

    cleanup_needed = False
    try:
        for name, revision in (
            ("baseline", "checkout-v1"),
            ("fault", "checkout-v2"),
            ("recovery", "checkout-v1"),
        ):
            # Start before activation so the incident window includes its deployment event.
            started_at = now()
            deployment = None
            if name == "fault":
                cleanup_needed = True  # Response loss can follow successful activation.
                deployment = activate(revision)
            elif name == "recovery":
                deployment = activate(revision)
                cleanup_needed = False
            samples = []
            for _ in range(requests):
                request_id = str(uuid4())
                started = perf_counter()
                response = client.post(
                    "/checkout",
                    json={"run_id": run_id, "delay_ms": 20},
                    headers={"X-Request-ID": request_id},
                )
                elapsed_ms = (perf_counter() - started) * 1000
                trace_id = response.headers.get("X-Trace-ID", "")
                if len(trace_id) != 32 or UUID(hex=trace_id).int == 0:
                    raise ValueError("Missing or invalid checkout trace ID")
                if response.headers.get("X-Request-ID") != request_id:
                    raise ValueError("Checkout did not preserve request ID")
                if response.headers.get("X-Simulator-Revision") != revision:
                    raise ValueError("Checkout revision did not match deployment")
                body = response.json()
                expected_status = 500 if name == "fault" else 200
                valid_body = (
                    body.get("error", {}).get("code") == "response_contract_error"
                    if name == "fault"
                    else body.get("status") == "completed" and body.get("run_id") == run_id
                )
                if response.status_code != expected_status or not valid_body:
                    raise ValueError("Unexpected checkout result during deployment scenario")
                samples.append(
                    {
                        "request_id": request_id,
                        "trace_id": trace_id,
                        "revision": revision,
                        "status_code": response.status_code,
                        "latency_ms": round(elapsed_ms, 3),
                    }
                )
            result["phases"].append(
                {
                    "name": name,
                    "run_id": run_id,
                    "started_at": started_at,
                    "ended_at": now(),
                    "deployment": deployment,
                    "samples": samples,
                    "median_latency_ms": median(sample["latency_ms"] for sample in samples),
                    "error_rate": sum(sample["status_code"] >= 500 for sample in samples)
                    / len(samples),
                }
            )
    finally:
        if cleanup_needed:
            activate("checkout-v1")
    result["ended_at"] = now()
    result["regression_observed"] = [phase["error_rate"] for phase in result["phases"]] == [0, 1, 0]
    return result


def run_pool_exhaustion(client: httpx.Client, *, requests: int = 12) -> dict:
    if not 4 <= requests <= 20:
        raise ValueError("pool exhaustion requests must be between 4 and 20")
    run_id = str(uuid4())

    def send(fault_mode: str) -> dict:
        request_id = str(uuid4())
        started = perf_counter()
        response = client.post(
            "/checkout",
            json={"run_id": run_id, "delay_ms": 20, "fault_mode": fault_mode},
            headers={"X-Request-ID": request_id},
        )
        return {
            "request_id": request_id,
            "trace_id": response.headers.get("X-Trace-ID", ""),
            "status_code": response.status_code,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
        }

    baseline_started = now()
    baseline = [send("none") for _ in range(min(4, requests))]
    baseline_ended = now()
    fault_started = now()
    with ThreadPoolExecutor(max_workers=requests) as executor:
        fault = list(executor.map(lambda _: send("pool_exhaustion"), range(requests)))
    fault_ended = now()
    result = {
        "schema_version": 1,
        "scenario": "pool_exhaustion",
        "execution": "http",
        "configuration": {"concurrent_requests": requests, "pool_capacity": 2},
        "started_at": baseline_started,
        "phases": [
            {"name": "baseline", "run_id": run_id, "started_at": baseline_started,
             "ended_at": baseline_ended, "samples": baseline},
            {"name": "fault", "run_id": run_id, "started_at": fault_started,
             "ended_at": fault_ended, "samples": fault},
        ],
        "pool_exhaustion_observed": any(item["status_code"] >= 500 for item in fault),
    }
    result["ended_at"] = fault_ended
    return result


def run_retry_storm(client: httpx.Client, *, requests: int = 5) -> dict:
    if not 2 <= requests <= 20:
        raise ValueError("retry storm requests must be between 2 and 20")
    run_id = str(uuid4())
    def phase(fault_mode: str, count: int) -> tuple[list[dict], list[int]]:
        started_at = now()
        samples, attempts = [], []
        for _ in range(count):
            request_id = str(uuid4())
            started = perf_counter()
            response = client.post(
                "/checkout",
                json={"run_id": run_id, "delay_ms": 20, "fault_mode": fault_mode},
                headers={"X-Request-ID": request_id},
            )
            attempts.append(int(response.headers.get("X-Simulator-Retry-Attempts", "0")))
            samples.append({
                "request_id": request_id,
                "trace_id": response.headers.get("X-Trace-ID", ""),
                "status_code": response.status_code,
                "latency_ms": round((perf_counter() - started) * 1000, 3),
            })
        return [{"name": "baseline" if fault_mode == "none" else "fault",
                 "run_id": run_id, "started_at": started_at, "ended_at": now(),
                 "samples": samples}], attempts

    baseline_phases, _ = phase("none", requests)
    fault_phases, observed_attempts = phase("retry_storm", requests)
    phases = [*baseline_phases, *fault_phases]
    fault_statuses = [sample["status_code"] for sample in fault_phases[0]["samples"]]
    result = {
        "schema_version": 1,
        "scenario": "retry_storm",
        "execution": "http",
        "configuration": {"requests_per_phase": requests, "attempts_per_request": 3},
        "started_at": phases[0]["started_at"],
        "phases": phases,
        "status_codes": fault_statuses,
        "observed_attempts_per_request": observed_attempts,
        "retry_storm_observed": all(code == 503 for code in fault_statuses)
        and all(count == 3 for count in observed_attempts),
        "ended_at": phases[-1]["ended_at"],
    }
    return result
