"""
TestSprite validation tools — called after a remediation action to confirm
the targeted service has returned to baseline. In DEMO_MODE, returns a
realistic stubbed response. In production, calls the TestSprite API.

Tools:
  - validate_service_recovery   — post-remediation smoke / regression test
  - validate_scale_stability    — network stability test across a scale event
                                  (scale-up OR scale-down + measure before/after)
"""
from __future__ import annotations

import json
import os
import time

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
async def validate_scale_stability(
    service_name: str,
    scale_direction: str,
    instance_count_before: int,
    instance_count_after: int,
    stabilization_wait_seconds: int = 30,
    test_suite: str = "network_stability",
) -> str:
    """
    Run a two-phase TestSprite network stability test around a scale event.
    Phase 1 (pre-scale): establish baseline pass rate and latency.
    Phase 2 (post-scale): measure again after stabilization to confirm
      the scale did not break network routing, service discovery, or
      introduce latency regression.

    Use this after scale_ecs_service (both scale-up AND scale-down) to
    confirm the cluster remains stable and tests still pass at the new
    replica count.

    Args:
        service_name:              Service being scaled
        scale_direction:           "up" or "down"
        instance_count_before:     Replica count before the scale
        instance_count_after:      Replica count after the scale
        stabilization_wait_seconds: Seconds to wait between phases (default 30)
        test_suite:                Which TestSprite suite to run (default "network_stability")

    Returns:
        JSON with pre/post metrics, stability verdict, and any regressions detected
    """
    if DEMO_MODE:
        # Phase 1: pre-scale baseline
        pre_p99   = 320.0
        pre_pass  = 49
        pre_total = 50

        # Simulate stabilization wait (shortened in demo)
        _demo_wait = min(stabilization_wait_seconds, 3)
        time.sleep(_demo_wait)

        # Phase 2: post-scale — slightly better on scale-up, slightly worse on scale-down
        if scale_direction == "up":
            post_p99  = pre_p99 * 0.85   # better throughput
            post_pass = 50
        else:
            post_p99  = pre_p99 * 1.08   # slight overhead from draining
            post_pass = 48

        post_total = 50

        latency_delta  = round(post_p99 - pre_p99, 1)
        pass_rate_delta = round((post_pass / post_total - pre_pass / pre_total) * 100, 1)
        stable = abs(latency_delta) < pre_p99 * 0.20 and post_pass / post_total >= 0.94

        result = {
            "service":            service_name,
            "scale_direction":    scale_direction,
            "instance_before":    instance_count_before,
            "instance_after":     instance_count_after,
            "stabilization_wait": stabilization_wait_seconds,
            "test_suite":         test_suite,
            "phase_1_pre_scale": {
                "passed":       pre_pass,
                "failed":       pre_total - pre_pass,
                "pass_rate":    round(pre_pass / pre_total * 100, 1),
                "p99_latency_ms": pre_p99,
            },
            "phase_2_post_scale": {
                "passed":       post_pass,
                "failed":       post_total - post_pass,
                "pass_rate":    round(post_pass / post_total * 100, 1),
                "p99_latency_ms": round(post_p99, 1),
            },
            "delta": {
                "latency_ms":     latency_delta,
                "pass_rate_pct":  pass_rate_delta,
            },
            "network_stable":  stable,
            "verdict":         "STABLE" if stable else "UNSTABLE — regression detected",
            "details": (
                f"[DEMO] Scale-{scale_direction} from {instance_count_before} → "
                f"{instance_count_after} replicas. "
                f"P99 {pre_p99}ms → {round(post_p99,1)}ms "
                f"({'improved' if latency_delta < 0 else 'regressed'} by {abs(latency_delta):.1f}ms). "
                f"Network stability: {'✅ STABLE' if stable else '❌ UNSTABLE'}."
            ),
        }
        return json.dumps(result)

    # ── Production path ───────────────────────────────────────────────────────
    async with httpx.AsyncClient(timeout=120.0) as client:
        headers = {"Authorization": f"Bearer {TESTSPRITE_API_KEY}"}

        # Phase 1
        pre_resp = await client.post(
            f"{TESTSPRITE_BASE_URL}/v1/validate",
            headers=headers,
            json={"service": service_name, "suite": test_suite, "phase": "pre_scale"},
        )
        pre_resp.raise_for_status()
        pre = pre_resp.json()

        # Wait for cluster stabilization
        time.sleep(stabilization_wait_seconds)

        # Phase 2
        post_resp = await client.post(
            f"{TESTSPRITE_BASE_URL}/v1/validate",
            headers=headers,
            json={
                "service":   service_name,
                "suite":     test_suite,
                "phase":     "post_scale",
                "scale_direction": scale_direction,
                "instance_count":  instance_count_after,
            },
        )
        post_resp.raise_for_status()
        post = post_resp.json()

    pre_p99   = pre.get("latency_p99_ms", 0)
    post_p99  = post.get("latency_p99_ms", 0)
    pre_rate  = pre.get("pass_rate", 0)
    post_rate = post.get("pass_rate", 0)
    stable    = post_rate >= pre_rate * 0.95 and post_p99 <= pre_p99 * 1.20

    return json.dumps({
        "service":            service_name,
        "scale_direction":    scale_direction,
        "instance_before":    instance_count_before,
        "instance_after":     instance_count_after,
        "stabilization_wait": stabilization_wait_seconds,
        "test_suite":         test_suite,
        "phase_1_pre_scale":  pre,
        "phase_2_post_scale": post,
        "delta": {
            "latency_ms":    round(post_p99 - pre_p99, 1),
            "pass_rate_pct": round(post_rate - pre_rate, 1),
        },
        "network_stable": stable,
        "verdict":        "STABLE" if stable else "UNSTABLE — regression detected",
        "details":        post.get("summary", ""),
    })
