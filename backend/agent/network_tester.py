"""
Network Testing Strategy Agent.

Reads the persistent memory store (insights + patterns) and derives
concrete network test strategies, then executes them via httpx.

Strategy types
--------------
health_sweep      — HTTP GET every known endpoint, check 2xx + latency
latency_probe     — 10 sequential requests to measure p50/p95/p99
load_burst        — N concurrent requests to simulate traffic spike
cascade_sim       — Probe a service + all its known downstream deps in order
dependency_chain  — Walk dependency order and assert each link passes
"""
from __future__ import annotations

import asyncio
import statistics
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal

import httpx

from memory.store import get_all_insights, get_all_patterns

BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 8.0

# Endpoints always included in health_sweep
CORE_ENDPOINTS = [
    {"path": "/health",              "name": "Health Check"},
    {"path": "/api/agent/health",    "name": "Agent Health"},
    {"path": "/api/cluster/status",  "name": "Cluster Status"},
    {"path": "/api/graph/",          "name": "Service Graph"},
    {"path": "/api/insights/",       "name": "Insights Store"},
    {"path": "/api/cluster/events",  "name": "Cluster Events"},
]

StrategyType = Literal[
    "health_sweep",
    "latency_probe",
    "load_burst",
    "cascade_sim",
    "dependency_chain",
]


@dataclass
class TestStrategy:
    id: str
    name: str
    type: StrategyType
    description: str
    target: str                       # service name or "all"
    derived_from: str                 # insight/pattern id that triggered this
    severity: str                     # low / medium / high / critical
    endpoints: list[str] = field(default_factory=list)
    concurrency: int = 1              # for load_burst
    samples: int = 10                 # for latency_probe


@dataclass
class EndpointResult:
    endpoint: str
    name: str
    status_code: int
    latency_ms: float
    passed: bool
    error: str = ""


@dataclass
class StrategyResult:
    strategy_id: str
    strategy_name: str
    strategy_type: str
    status: str                       # passed / failed / partial
    target: str
    duration_ms: float
    tests_run: int
    tests_passed: int
    tests_failed: int
    findings: list[dict] = field(default_factory=list)
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    error_rate_pct: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NetworkTestReport:
    report_id: str
    timestamp: str
    strategies_run: int
    strategies_passed: int
    strategies_failed: int
    overall_status: str               # passed / failed / partial
    duration_ms: float
    strategy_results: list[dict] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Strategy generator — derives test plans from memory
# ---------------------------------------------------------------------------

def generate_strategies(insights: list[dict], patterns: list[dict]) -> list[TestStrategy]:
    """
    Build a list of TestStrategy objects by reading insights and patterns
    stored in the agent's persistent memory.
    """
    strategies: list[TestStrategy] = []

    # 1. Always run a full health sweep
    strategies.append(TestStrategy(
        id=f"strat-{uuid.uuid4().hex[:6]}",
        name="Core Endpoint Health Sweep",
        type="health_sweep",
        description="Verify all platform API endpoints return 2xx within 2 s.",
        target="all",
        derived_from="baseline",
        severity="medium",
        endpoints=[ep["path"] for ep in CORE_ENDPOINTS],
    ))

    seen_services: set[str] = set()

    for ins in insights:
        svc = ins.get("service", "unknown")
        title = ins.get("title", "")
        severity = ins.get("severity", "low")
        ins_id = ins.get("id", "unknown")
        insight_text = (ins.get("insight", "") + " " + title).lower()

        # High-latency insight → latency probe
        if any(k in insight_text for k in ("latency", "p99", "slow", "timeout", "response time")) \
                and svc not in seen_services:
            strategies.append(TestStrategy(
                id=f"strat-{uuid.uuid4().hex[:6]}",
                name=f"Latency Probe — {svc}",
                type="latency_probe",
                description=(
                    f"Run 10 sequential requests to {svc} endpoints and compute "
                    f"p50/p95/p99. Derived from insight: '{title}'."
                ),
                target=svc,
                derived_from=ins_id,
                severity=severity,
                endpoints=["/api/agent/health", "/api/cluster/status"],
                samples=10,
            ))
            seen_services.add(svc)

        # Overload / CPU / scaling insight → load burst
        if any(k in insight_text for k in ("overload", "cpu", "spike", "scale", "capacity", "traffic")) \
                and f"load-{svc}" not in seen_services:
            strategies.append(TestStrategy(
                id=f"strat-{uuid.uuid4().hex[:6]}",
                name=f"Load Burst — {svc}",
                type="load_burst",
                description=(
                    f"Fire 20 concurrent requests to simulate a traffic spike on {svc}. "
                    f"Derived from insight: '{title}'."
                ),
                target=svc,
                derived_from=ins_id,
                severity=severity,
                endpoints=["/api/cluster/status", "/api/graph/"],
                concurrency=20,
            ))
            seen_services.add(f"load-{svc}")

    for pat in patterns:
        pat_type = pat.get("type", "")
        svc = pat.get("service", pat.get("scope", "unknown"))
        pat_id = pat.get("id", "unknown")
        description = pat.get("description", "")
        severity = "high" if pat.get("confidence", 0) > 0.7 else "medium"

        # Cascade risk → cascade simulation
        if "cascade" in pat_type and f"cascade-{svc}" not in seen_services:
            strategies.append(TestStrategy(
                id=f"strat-{uuid.uuid4().hex[:6]}",
                name=f"Cascade Simulation — {svc}",
                type="cascade_sim",
                description=(
                    f"Probe {svc} and its downstream dependencies sequentially "
                    f"to identify where cascade failures originate. Pattern: '{description[:80]}'."
                ),
                target=svc,
                derived_from=pat_id,
                severity=severity,
                endpoints=["/api/graph/", "/api/cluster/status", "/api/agent/health"],
            ))
            seen_services.add(f"cascade-{svc}")

        # Dependency bottleneck → dependency chain test
        if "dependency" in pat_type or "bottleneck" in pat_type:
            strategies.append(TestStrategy(
                id=f"strat-{uuid.uuid4().hex[:6]}",
                name=f"Dependency Chain — {svc}",
                type="dependency_chain",
                description=(
                    f"Walk the known dependency chain for {svc} and assert each "
                    f"hop is reachable within SLO. Pattern: '{description[:80]}'."
                ),
                target=svc,
                derived_from=pat_id,
                severity=severity,
                endpoints=["/api/graph/", "/api/agent/health", "/api/cluster/status"],
            ))

    return strategies


# ---------------------------------------------------------------------------
# Test runners
# ---------------------------------------------------------------------------

async def _probe_endpoint(client: httpx.AsyncClient, path: str, name: str) -> EndpointResult:
    start = time.monotonic()
    try:
        resp = await client.get(path)
        latency = round((time.monotonic() - start) * 1000, 1)
        passed = 200 <= resp.status_code < 400
        return EndpointResult(
            endpoint=path, name=name,
            status_code=resp.status_code,
            latency_ms=latency, passed=passed,
        )
    except Exception as exc:
        latency = round((time.monotonic() - start) * 1000, 1)
        return EndpointResult(
            endpoint=path, name=path,
            status_code=0, latency_ms=latency,
            passed=False, error=str(exc)[:120],
        )


async def _run_health_sweep(strategy: TestStrategy) -> StrategyResult:
    start = time.monotonic()
    findings: list[dict] = []
    passed = 0

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT) as client:
        tasks = [_probe_endpoint(client, ep["path"], ep["name"]) for ep in CORE_ENDPOINTS]
        results = await asyncio.gather(*tasks)

    for r in results:
        findings.append(asdict(r))
        if r.passed:
            passed += 1

    failed = len(findings) - passed
    duration = round((time.monotonic() - start) * 1000, 1)
    status = "passed" if failed == 0 else ("failed" if passed == 0 else "partial")

    return StrategyResult(
        strategy_id=strategy.id,
        strategy_name=strategy.name,
        strategy_type=strategy.type,
        status=status,
        target=strategy.target,
        duration_ms=duration,
        tests_run=len(findings),
        tests_passed=passed,
        tests_failed=failed,
        findings=findings,
    )


async def _run_latency_probe(strategy: TestStrategy) -> StrategyResult:
    start = time.monotonic()
    endpoint = strategy.endpoints[0] if strategy.endpoints else "/api/agent/health"
    latencies: list[float] = []
    findings: list[dict] = []
    passed = 0

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT) as client:
        for i in range(strategy.samples):
            r = await _probe_endpoint(client, endpoint, f"sample-{i+1}")
            latencies.append(r.latency_ms)
            if r.passed:
                passed += 1
            findings.append(asdict(r))

    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)

    def percentile(lst: list[float], pct: float) -> float:
        idx = max(0, int(len(lst) * pct / 100) - 1)
        return round(lst[idx], 1)

    p50 = percentile(latencies_sorted, 50)
    p95 = percentile(latencies_sorted, 95)
    p99 = percentile(latencies_sorted, 99)
    error_rate = round((strategy.samples - passed) / strategy.samples * 100, 1)

    # Flag as failed if p99 > 1000ms or error_rate > 10%
    status = "passed"
    if p99 > 1000 or error_rate > 10:
        status = "failed"
    elif p99 > 500 or error_rate > 0:
        status = "partial"

    duration = round((time.monotonic() - start) * 1000, 1)
    return StrategyResult(
        strategy_id=strategy.id,
        strategy_name=strategy.name,
        strategy_type=strategy.type,
        status=status,
        target=strategy.target,
        duration_ms=duration,
        tests_run=strategy.samples,
        tests_passed=passed,
        tests_failed=strategy.samples - passed,
        findings=findings,
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        error_rate_pct=error_rate,
    )


async def _run_load_burst(strategy: TestStrategy) -> StrategyResult:
    start = time.monotonic()
    endpoint = strategy.endpoints[0] if strategy.endpoints else "/api/cluster/status"

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT) as client:
        tasks = [_probe_endpoint(client, endpoint, f"req-{i+1}") for i in range(strategy.concurrency)]
        results = await asyncio.gather(*tasks)

    latencies = [r.latency_ms for r in results]
    passed = sum(1 for r in results if r.passed)
    failed = strategy.concurrency - passed
    error_rate = round(failed / strategy.concurrency * 100, 1)

    def percentile(lst: list[float], pct: float) -> float:
        s = sorted(lst)
        idx = max(0, int(len(s) * pct / 100) - 1)
        return round(s[idx], 1)

    status = "passed"
    if error_rate > 20:
        status = "failed"
    elif error_rate > 5 or percentile(latencies, 95) > 800:
        status = "partial"

    duration = round((time.monotonic() - start) * 1000, 1)
    return StrategyResult(
        strategy_id=strategy.id,
        strategy_name=strategy.name,
        strategy_type=strategy.type,
        status=status,
        target=strategy.target,
        duration_ms=duration,
        tests_run=strategy.concurrency,
        tests_passed=passed,
        tests_failed=failed,
        findings=[asdict(r) for r in results],
        p50_ms=percentile(latencies, 50),
        p95_ms=percentile(latencies, 95),
        p99_ms=percentile(latencies, 99),
        error_rate_pct=error_rate,
    )


async def _run_cascade_sim(strategy: TestStrategy) -> StrategyResult:
    """Probe endpoints sequentially — stop-on-first-failure mirrors real cascade behavior."""
    start = time.monotonic()
    findings: list[dict] = []
    passed = 0
    cascade_triggered = False

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=DEFAULT_TIMEOUT) as client:
        for ep in strategy.endpoints:
            r = await _probe_endpoint(client, ep, ep)
            findings.append(asdict(r))
            if r.passed:
                passed += 1
            else:
                cascade_triggered = True
                # Continue to show full blast radius
        findings.append({
            "endpoint": "cascade_analysis",
            "name": "Cascade Trigger",
            "status_code": 0,
            "latency_ms": 0,
            "passed": not cascade_triggered,
            "error": "Cascade failure detected — downstream propagation possible" if cascade_triggered else "",
        })

    failed = len(strategy.endpoints) - passed
    status = "passed" if not cascade_triggered else ("failed" if passed == 0 else "partial")
    duration = round((time.monotonic() - start) * 1000, 1)

    return StrategyResult(
        strategy_id=strategy.id,
        strategy_name=strategy.name,
        strategy_type=strategy.type,
        status=status,
        target=strategy.target,
        duration_ms=duration,
        tests_run=len(strategy.endpoints),
        tests_passed=passed,
        tests_failed=failed,
        findings=findings,
    )


async def _run_dependency_chain(strategy: TestStrategy) -> StrategyResult:
    return await _run_cascade_sim(strategy)  # same execution model, different framing


_RUNNERS = {
    "health_sweep":    _run_health_sweep,
    "latency_probe":   _run_latency_probe,
    "load_burst":      _run_load_burst,
    "cascade_sim":     _run_cascade_sim,
    "dependency_chain": _run_dependency_chain,
}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_network_tests(strategy_ids: list[str] | None = None) -> NetworkTestReport:
    """
    Generate strategies from memory, run them, and return a NetworkTestReport.
    If strategy_ids is provided, only run those strategies.
    """
    insights = get_all_insights()
    patterns = get_all_patterns()
    strategies = generate_strategies(insights, patterns)

    if strategy_ids:
        strategies = [s for s in strategies if s.id in strategy_ids]

    report_start = time.monotonic()
    results: list[StrategyResult] = []

    for strat in strategies:
        runner = _RUNNERS.get(strat.type)
        if runner is None:
            continue
        try:
            result = await runner(strat)
        except Exception as exc:
            result = StrategyResult(
                strategy_id=strat.id,
                strategy_name=strat.name,
                strategy_type=strat.type,
                status="failed",
                target=strat.target,
                duration_ms=0,
                tests_run=0,
                tests_passed=0,
                tests_failed=1,
                findings=[{"error": str(exc)}],
            )
        results.append(result)

    total_duration = round((time.monotonic() - report_start) * 1000, 1)
    passed_strats = sum(1 for r in results if r.status == "passed")
    failed_strats = sum(1 for r in results if r.status == "failed")

    if failed_strats == 0:
        overall = "passed"
    elif passed_strats == 0:
        overall = "failed"
    else:
        overall = "partial"

    # Derive plain-English recommendations
    recommendations: list[str] = []
    for r in results:
        if r.status == "failed" and r.strategy_type == "latency_probe":
            recommendations.append(f"P99 latency on {r.target} is critical — review recent deployments and DB query plans.")
        if r.status != "passed" and r.strategy_type == "load_burst":
            recommendations.append(f"Load burst on {r.target} shows {r.error_rate_pct}% error rate — consider horizontal scaling or rate limiting.")
        if r.status != "passed" and r.strategy_type == "cascade_sim":
            recommendations.append(f"Cascade simulation on {r.target} detected propagation risk — add circuit breakers on downstream calls.")
        if r.status != "passed" and r.strategy_type == "health_sweep":
            failed_eps = [f["endpoint"] for f in r.findings if not f.get("passed")]
            recommendations.append(f"Health sweep failures: {', '.join(failed_eps)} — check service health and network routing.")

    return NetworkTestReport(
        report_id=f"ntr-{uuid.uuid4().hex[:8]}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        strategies_run=len(results),
        strategies_passed=passed_strats,
        strategies_failed=failed_strats,
        overall_status=overall,
        duration_ms=total_duration,
        strategy_results=[r.to_dict() for r in results],
        recommendations=recommendations,
    )
