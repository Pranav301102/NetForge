"""
TestSprite validation tool — called after a remediation action to confirm
the targeted service has returned to baseline. In DEMO_MODE, returns a
realistic stubbed response. In production, calls the TestSprite API.
"""
from __future__ import annotations

import json
import os

import httpx
from strands import tool

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
TESTSPRITE_API_KEY = os.getenv("TESTSPRITE_API_KEY", "")
TESTSPRITE_BASE_URL = os.getenv("TESTSPRITE_BASE_URL", "https://api.testsprite.com")


@tool
async def validate_service_recovery(
    service_name: str,
    baseline_p99_ms: float,
    test_suite: str = "smoke",
) -> str:
    """
    Run a TestSprite validation suite against a service after remediation to
    confirm it has recovered to baseline performance. Should be called after
    any scale_ecs_service or trigger_codedeploy_rollback action.

    Args:
        service_name: Service to validate (e.g., "payment-service")
        baseline_p99_ms: Expected p99 latency at healthy baseline (e.g., 400.0)
        test_suite: Which test suite to run — "smoke", "regression", or "full"

    Returns:
        JSON ValidationResult with pass rate, current p99, and recovery status
    """
    if DEMO_MODE:
        # Simulate recovery: payment-service dropped from 1800ms to 380ms
        current_p99 = 380.0 if service_name == "payment-service" else baseline_p99_ms * 0.95
        passed = 47
        total = 50
        result = {
            "service": service_name,
            "test_suite": test_suite,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total * 100, 1),
            "latency_p99_ms": current_p99,
            "baseline_p99_ms": baseline_p99_ms,
            "recovered": current_p99 <= baseline_p99_ms * 1.1,
            "details": (
                f"[DEMO] TestSprite ran {total} tests against {service_name}. "
                f"p99 latency dropped from 1800ms → {current_p99}ms "
                f"(baseline: {baseline_p99_ms}ms). Service is RECOVERED."
            ),
        }
        return json.dumps(result)

    # Production path
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{TESTSPRITE_BASE_URL}/v1/validate",
            headers={"Authorization": f"Bearer {TESTSPRITE_API_KEY}"},
            json={
                "service": service_name,
                "suite": test_suite,
                "baseline_p99_ms": baseline_p99_ms,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return json.dumps({
        "service": service_name,
        "test_suite": test_suite,
        "passed": data.get("passed", 0),
        "failed": data.get("failed", 0),
        "pass_rate": data.get("pass_rate", 0),
        "latency_p99_ms": data.get("latency_p99_ms", 0),
        "baseline_p99_ms": baseline_p99_ms,
        "recovered": data.get("recovered", False),
        "details": data.get("summary", ""),
    })


@tool
async def validate_network_health(trigger: str = "manual") -> str:
    """
    Run network validation against all Forge API endpoints after scale events.
    Tests /health, /api/agent/health, /api/cluster/status, and /api/graph/
    to verify the cluster is functioning correctly.

    Args:
        trigger: What triggered the validation — "scale_up", "scale_down", or "manual"

    Returns:
        JSON with endpoints tested, pass/fail counts, TestSprite test results, and overall status
    """
    from cluster.validation import validate_network_after_scale

    result = await validate_network_after_scale(
        trigger_event=trigger,
        trigger_replica="agent-tool",
    )
    return json.dumps(result.to_dict())
