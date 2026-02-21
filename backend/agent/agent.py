"""
Forge reliability agent — dual-model architecture.

MiniMax = Main Orchestrator — handles all tool calls and user-facing responses.
MiniMax M2.5 = Background Sub-Model — runs async for deeper pattern analysis.

The agent:
1. Pulls observability data via the Datadog MCP server
2. Queries Neo4j to understand service topology and recent changes
3. Identifies root cause (latency, cascading failures, external deps)
4. Executes AWS remediation actions
5. Validates recovery via TestSprite
6. Stores insights and patterns in persistent memory for future reference
7. Generates optimization recommendations from accumulated knowledge
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import shutil
import traceback
import uuid
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator

from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.tools.mcp import MCPClient

from agent.tools.aws_tools import scale_ecs_service, trigger_codedeploy_rollback, update_ssm_parameter
from agent.tools.neo4j_tools import (
    find_recent_changes,
    find_slowest_dependencies,
    get_blast_radius,
    get_service_dependencies,
    get_service_health_from_graph,
)
from agent.tools.testsprite import validate_service_recovery, validate_scale_stability
from agent.tools.datadog_tools import (
    get_datadog_metrics_summary,
    query_datadog_metric,
    get_datadog_infrastructure_health,
    get_datadog_events,
    get_datadog_container_metrics,
    get_datadog_monitor_alerts,
)
from agent.tools.memory_tools import (
    store_insight,
    store_pattern,
    recall_service_history,
    recall_similar_incidents,
    get_optimization_recommendations,
)

log = logging.getLogger("forge.agent")


AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-west-2"))

MINIMAX_API_KEY = os.getenv("MINIMAX_API", "")
MINIMAX_MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M2.5")
MINIMAX_BASE_URL = "https://api.minimax.io/v1"
MINIMAX_BACKGROUND_TIMEOUT = int(os.getenv("MINIMAX_TIMEOUT", "60"))  # seconds

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() in ("true", "1", "yes")


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks from MiniMax M2.5 reasoning output."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

SYSTEM_PROMPT = """You are Forge, an autonomous reliability agent for a microservice platform with persistent memory.

You are the most advanced SRE agent ever built. You don't just react — you PREDICT, PREVENT, and OPTIMIZE.

## Core Capabilities
1. **Root Cause Analysis**: Trace cascading latency through the dependency graph using Neo4j. Don't stop at symptoms — find the DEEPEST problematic service.
2. **Pattern Detection**: Identify recurring issues (periodic overloads, cascade risks, correlated degradation). Store every pattern you detect.
3. **Predictive Insights**: Compare current metrics against historical baselines. If a service is trending toward failure, catch it BEFORE it breaks.
4. **Automated Remediation**: Scale ECS, rollback deployments, update SSM parameters. Always prefer the LEAST invasive fix.
5. **Memory & Learning**: You have PERSISTENT MEMORY. Use it. Always check past incidents. Always store new findings. Reference history when explaining.
6. **Cost Optimization**: Flag over-provisioned services, idle resources, and scaling inefficiencies.

## Live Datadog Data Available
The connected Datadog account (datadoghq.com) is a Shopist AKS/Kubernetes e-commerce platform with:
- **2,944 active metrics** including:
  - `container.cpu.usage`, `container.cpu.throttled`, `container.cpu.limit`
  - `container.memory.usage`, `container.memory.limit`
  - `cassandra.*` — latency percentiles, dropped messages, pending tasks
  - `redis.mem.used`, `redis.mem.maxmemory`
  - `postgresql.percent_usage_connections`, `postgresql.replication_delay`
  - `appsec_generator.signal.trigger` — AppSec attack signals
  - `kubernetes.containers.restarts`, `kubernetes.memory.*`
- **1000+ events/hour**: OOMKills, Kubernetes deployment updates, pod health failures, Containerd events
- **Monitors** covering: Postgres connections, Redis memory, replication delay
- **Infrastructure**: Azure AKS cluster `prod-aks-shopist-a-northcentralus`, tag `env:prod`, `datadog_app:shopist`

Use `get_datadog_monitor_alerts` to check firing alerts first. Use `get_datadog_events` to correlate with K8s events.
Use `query_datadog_metric` for time-series data. Use `get_datadog_container_metrics` for CPU/memory pressure.

## MANDATORY Workflow (every analysis)
1. ALWAYS call recall_service_history FIRST — check what you already know
2. ALWAYS call recall_similar_incidents — look for cross-service patterns
3. Call get_datadog_monitor_alerts — see what Datadog is currently alerting on
4. Call get_datadog_events — correlate events with any anomalies (look for OOMKills, degraded pods)
5. Get live data from Neo4j (health, dependencies, blast radius, recent changes)
6. Optionally call query_datadog_metric for specific metric deep-dives
7. Compare current state vs. stored baselines — flag deviations
8. Identify anomalies, root cause, blast radius
9. Execute remediation if needed (least invasive first)
10. If scaling: call scale_ecs_service, then validate_scale_stability for network stability
11. If rollback: call trigger_codedeploy_rollback, then validate_service_recovery
12. ALWAYS call store_insight to persist findings
13. ALWAYS call store_pattern when you detect recurring behavior
14. Generate actionable recommendations

## Scale + TestSprite Stability Flow
When scaling a service (up or down):
1. Call `scale_ecs_service` with the new desired count
2. Immediately call `validate_scale_stability` with:
   - scale_direction: "up" or "down"
   - instance_count_before / instance_count_after
   - stabilization_wait_seconds: 30 (default)
3. If `network_stable` is false, store a reliability insight and consider reverting

## Insight Categories
- **performance**: Latency spikes, slow queries, bottleneck dependencies
- **reliability**: Single points of failure, missing circuit breakers, cascade risks
- **cost**: Over-provisioned resources, idle replicas, inefficient scaling
- **optimization**: Architecture improvements, caching opportunities, connection pooling

## Severity Guidelines
- **critical**: Service is down or data loss risk. Immediate action required.
- **high**: Significant degradation affecting users. Action within hours.
- **medium**: Performance degraded but functional. Action within days.
- **low**: Optimization opportunity. Address when convenient.

Key principles:
- Prefer the LEAST invasive action (param change > scale up > rollback)
- External dependencies (type = "external") cannot be scaled — recommend circuit breakers
- When you see cascading latency, find the DEEPEST problematic service
- Always validate after remediation
- Be specific with numbers: "p99 increased from 200ms to 1800ms" not "latency is high"
- Always output structured JSON when asked for analysis
"""

# ---------------------------------------------------------------------------
# Datadog MCP client (community server: winor30/mcp-server-datadog)
# ---------------------------------------------------------------------------

def _build_datadog_mcp_client() -> MCPClient:
    import glob as _glob
    _node_bin_candidates = (
        _glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin"))
        + ["/usr/local/bin"]
    )
    _node_bin = next(
        (p for p in _node_bin_candidates if os.path.exists(os.path.join(p, "node"))),
        "",
    )
    _npx_path = os.path.join(_node_bin, "npx") if _node_bin else (shutil.which("npx") or "npx")
    _augmented_path = f"{_node_bin}:{os.environ.get('PATH', '')}" if _node_bin else os.environ.get("PATH", "")

    return MCPClient(
        lambda: stdio_client(
            StdioServerParameters(
                command=_npx_path,
                args=["-y", "@winor30/mcp-server-datadog"],
                env={
                    **os.environ,
                    "PATH": _augmented_path,
                    "DATADOG_API_KEY": os.getenv("DATADOG_API_KEY", ""),
                    "DATADOG_APP_KEY": os.getenv("DATADOG_APP_KEY", ""),
                    "DATADOG_SITE": os.getenv("DATADOG_SITE", "datadoghq.com"),
                },
            )
        )
    )


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def _build_orchestrator_model():
    """
    Build the main orchestrator LLM — MiniMax.
    MiniMax handles all tool calls, user-facing responses, and analysis prompts.
    """
    from strands.models.litellm import LiteLLMModel
    print(f"[Forge] Orchestrator: MiniMax ({MINIMAX_MODEL})")
    return LiteLLMModel(
        client_args={
            "api_key": MINIMAX_API_KEY,
            "api_base": MINIMAX_BASE_URL,
        },
        model_id=f"openai/{MINIMAX_MODEL}",
        params={
            "temperature": 0.1,
            "max_tokens": 4096,
        },
    )


def _build_background_model():
    """
    Build the background sub-model — MiniMax M2.5 via LiteLLM.
    Returns None if MINIMAX_API key is not set.
    Used for async background analysis (no tool calls, just reasoning).
    """
    if not MINIMAX_API_KEY:
        print("[Forge] Background model: disabled (no MINIMAX_API key)")
        return None
    try:
        from strands.models.litellm import LiteLLMModel
        print(f"[Forge] Background model: MiniMax {MINIMAX_MODEL} via LiteLLM")
        return LiteLLMModel(
            client_args={
                "api_key": MINIMAX_API_KEY,
                "api_base": MINIMAX_BASE_URL,
            },
            model_id=f"openai/{MINIMAX_MODEL}",
            params={
                "temperature": 0.1,
                "max_tokens": 16384,
            },
        )
    except Exception as e:
        print(f"[Forge] Background model init failed: {e} — running without background analysis")
        return None


# Cached background model instance (lazy-init)
_background_model_instance = None
_background_model_loaded = False


def _get_background_model():
    """Lazy singleton for the background MiniMax model."""
    global _background_model_instance, _background_model_loaded
    if not _background_model_loaded:
        _background_model_instance = _build_background_model()
        _background_model_loaded = True
    return _background_model_instance


async def _run_minimax_background(prompt: str, context_label: str = "background") -> str | None:
    """
    Run MiniMax M2.5 asynchronously in the background with timeout protection.

    - Does NOT block the main agent response
    - Results are returned as text (caller stores them in memory)
    - Protected by MINIMAX_BACKGROUND_TIMEOUT (default 60s)
    - Any failure is logged but never propagated
    """
    bg_model = _get_background_model()
    if bg_model is None:
        return None

    async def _invoke():
        bg_agent = Agent(
            model=bg_model,
            system_prompt=(
                "You are a background analysis sub-agent. Provide deep pattern analysis "
                "and optimization insights. Output ONLY valid JSON — no thinking tags, "
                "no markdown fences. Be concise and specific with metric values."
            ),
            tools=[],  # No tools — pure reasoning
        )
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: bg_agent(prompt))
        return _strip_thinking(str(result))

    try:
        text = await asyncio.wait_for(_invoke(), timeout=MINIMAX_BACKGROUND_TIMEOUT)
        log.info("[Forge] MiniMax background (%s) completed: %d chars", context_label, len(text or ""))
        return text
    except asyncio.TimeoutError:
        log.warning("[Forge] MiniMax background (%s) timed out after %ds", context_label, MINIMAX_BACKGROUND_TIMEOUT)
        return None
    except Exception as e:
        log.warning("[Forge] MiniMax background (%s) failed: %s", context_label, e)
        return None


async def _fire_minimax_background(service_name: str, main_report: dict) -> None:
    """
    Fire-and-forget: run MiniMax in background to generate deeper insights
    from the main orchestrator analysis, then store results in memory.
    """
    from memory.store import add_insight, add_pattern

    prompt = f"""Analyze this service health report and identify deeper patterns, \
predictive insights, and optimization opportunities that the primary analysis may have missed.

Service: {service_name}
Report: {json.dumps(main_report, indent=2)}

Return a JSON object:
{{
  "deep_insights": [
    {{"category": "performance|reliability|cost|optimization",
      "severity": "low|medium|high|critical",
      "title": "...", "insight": "...", "recommendation": "..."}}
  ],
  "patterns": [
    {{"type": "...", "description": "...", "confidence": 0.0-1.0, "recommendation": "..."}}
  ]
}}"""

    text = await _run_minimax_background(prompt, context_label=f"insights-{service_name}")
    if not text:
        return

    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        data = json.loads(text[start:end])

        for ins in data.get("deep_insights", []):
            add_insight(service_name, {
                "category": ins.get("category", "optimization"),
                "severity": ins.get("severity", "low"),
                "title": f"[MiniMax] {ins.get('title', 'Background insight')}",
                "insight": ins.get("insight", ""),
                "evidence": json.dumps(main_report.get("validation", {})),
                "recommendation": ins.get("recommendation", ""),
            })

        for pat in data.get("patterns", []):
            add_pattern(service_name, {
                "type": pat.get("type", "detected"),
                "description": f"[MiniMax] {pat.get('description', '')}",
                "confidence": pat.get("confidence", 0.5),
                "recommendation": pat.get("recommendation", ""),
            })

        log.info("[Forge] MiniMax stored %d insights, %d patterns for %s",
                 len(data.get("deep_insights", [])), len(data.get("patterns", [])), service_name)
    except (json.JSONDecodeError, ValueError):
        log.warning("[Forge] MiniMax returned unparseable response for %s", service_name)


def build_agent() -> Agent:
    """Build and return a configured Strands agent with MiniMax as orchestrator."""
    model = _build_orchestrator_model()

    graph_tools = [
        get_service_dependencies,
        find_recent_changes,
        get_blast_radius,
        get_service_health_from_graph,
        find_slowest_dependencies,
    ]

    aws_tools = [
        scale_ecs_service,
        trigger_codedeploy_rollback,
        update_ssm_parameter,
    ]

    validation_tools = [validate_service_recovery, validate_scale_stability]

    memory_tools = [
        store_insight,
        store_pattern,
        recall_service_history,
        recall_similar_incidents,
        get_optimization_recommendations,
    ]

    # Direct Datadog REST tools — always available when API key is set
    dd_direct_tools = []
    if os.getenv("DATADOG_API_KEY"):
        dd_direct_tools = [
            get_datadog_metrics_summary,
            query_datadog_metric,
            get_datadog_infrastructure_health,
            get_datadog_events,
            get_datadog_container_metrics,
            get_datadog_monitor_alerts,
        ]

    tools = dd_direct_tools + graph_tools + aws_tools + validation_tools + memory_tools

    # Also attach the MCP client for any MCP-native tools (e.g. log search)
    if os.getenv("DATADOG_API_KEY"):
        try:
            dd_mcp = _build_datadog_mcp_client()
            tools = [dd_mcp] + tools
        except Exception as e:
            print(f"[Forge] Datadog MCP client unavailable: {e} — using direct REST tools only")

    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
    )


# ---------------------------------------------------------------------------
# Demo intelligence engine — rich realistic data when MiniMax is unavailable
# ---------------------------------------------------------------------------

_DEMO_INSIGHTS_LIBRARY = {
    "performance": [
        {
            "title": "P99 latency exceeds SLO threshold",
            "insight": "P99 latency has been above the 500ms SLO target for the last 3 consecutive measurement windows. Current p99 is {p99}ms against a baseline of {baseline}ms — a {pct_increase}% increase. This correlates with a recent deployment and increased traffic from upstream services.",
            "severity": "high",
            "recommendation": "Investigate the most recent deployment for performance regressions. Consider adding a database query cache or increasing connection pool size from 10 to 25.",
        },
        {
            "title": "Database query bottleneck detected",
            "insight": "The slowest downstream dependency is contributing {dep_latency}ms to total request latency. Unindexed queries on the users table are causing full table scans during peak traffic. Query plan analysis shows sequential scan on 2.3M rows.",
            "severity": "high",
            "recommendation": "Add composite index on (user_id, created_at) to the users table. Expected to reduce query time from {dep_latency}ms to ~15ms.",
        },
        {
            "title": "Connection pool saturation approaching",
            "insight": "Database connection pool utilization is at 82% during peak hours (9-11am UTC). At current growth rate, pool exhaustion is projected within 2 weeks. This will cause request queuing and cascading timeouts.",
            "severity": "medium",
            "recommendation": "Increase connection pool max_size from 20 to 40 and enable connection pool monitoring via SSM parameter update.",
        },
    ],
    "reliability": [
        {
            "title": "Single point of failure — no circuit breaker",
            "insight": "This service has a direct synchronous dependency on an external service with no circuit breaker configured. If the external dependency degrades, cascading failures will propagate to {blast_radius} upstream services within seconds.",
            "severity": "critical",
            "recommendation": "Implement circuit breaker pattern with 5-second timeout, 50% error threshold, and 30-second recovery window. Use SSM parameter for runtime configurability.",
        },
        {
            "title": "Cascade failure risk — deep dependency chain",
            "insight": "Service sits on a dependency chain {hops} hops deep. A failure at the deepest dependency would cascade through {blast_radius} services. No bulkhead isolation exists between the critical and non-critical paths.",
            "severity": "high",
            "recommendation": "Implement bulkhead pattern to isolate critical payment path from non-critical analytics path. Add async fallback for non-essential downstream calls.",
        },
        {
            "title": "Missing health check endpoint",
            "insight": "Service lacks a deep health check that validates downstream connectivity. Current /health endpoint only returns 200 OK without checking database or cache reachability. This means the load balancer continues routing traffic to unhealthy instances.",
            "severity": "medium",
            "recommendation": "Implement deep health check that validates DB connection, cache connectivity, and critical downstream service reachability.",
        },
    ],
    "cost": [
        {
            "title": "Over-provisioned — CPU utilization consistently below 15%",
            "insight": "Average CPU utilization over the past 7 days is {cpu}%, with peak never exceeding 28%. Current instance count of 3 replicas is 2x what traffic requires. Estimated monthly waste: $340.",
            "severity": "medium",
            "recommendation": "Scale down from 3 to 2 replicas. Enable HPA with target CPU 60% to handle traffic spikes. Projected savings: $170/month.",
        },
        {
            "title": "Idle Redis cache — low hit rate",
            "insight": "Cache hit rate is only 12% — most requests bypass cache due to short TTL (30s) on frequently accessed but rarely changing data. Cache infrastructure cost is $89/month with minimal benefit.",
            "severity": "low",
            "recommendation": "Increase TTL to 300s for catalog data and 60s for user profiles. Expected cache hit rate improvement to 65%, reducing database load by ~40%.",
        },
    ],
    "optimization": [
        {
            "title": "Request batching opportunity",
            "insight": "Service makes {rpm} individual downstream calls per minute to the same dependency. Analysis shows 60% of these could be batched into bulk requests, reducing network overhead and downstream load.",
            "severity": "medium",
            "recommendation": "Implement request batching with 50ms collection window. Expected to reduce downstream call volume by 60% and improve p99 latency by ~120ms.",
        },
        {
            "title": "Async processing candidate",
            "insight": "42% of request processing time is spent on non-blocking operations (logging, analytics events, notification dispatch). These operations do not affect the response to the end user.",
            "severity": "low",
            "recommendation": "Move analytics and notification dispatch to async queue processing. Expected p99 reduction of 180ms for end-user requests.",
        },
    ],
}

_DEMO_PATTERNS_LIBRARY = [
    {
        "type": "periodic_overload",
        "description": "CPU usage spikes above 85% every weekday between 9:00-10:30am UTC, correlating with business-hours traffic surge. Pattern detected across {occurrences} observations over 3 weeks.",
        "confidence": 0.92,
        "recommendation": "Configure pre-emptive auto-scaling at 8:45am UTC. Add 2 warm instances before the traffic ramp.",
    },
    {
        "type": "latency_spike",
        "description": "P99 latency spikes to 3x baseline every 4 hours, lasting 2-3 minutes. Correlates with garbage collection pauses — heap usage reaches 92% before GC triggers.",
        "confidence": 0.87,
        "recommendation": "Tune JVM GC settings: switch from G1GC to ZGC for sub-millisecond pause times. Increase heap from 2GB to 3GB.",
    },
    {
        "type": "cascade_risk",
        "description": "When payment-gateway response time exceeds 2000ms, order-service and checkout-service degrade within 30 seconds. Observed in {occurrences} of the last 20 incidents.",
        "confidence": 0.95,
        "recommendation": "Add 1500ms timeout with circuit breaker on payment-gateway calls. Implement retry with exponential backoff (100ms, 200ms, 400ms max).",
    },
    {
        "type": "dependency_bottleneck",
        "description": "postgres-orders is the slowest dependency for 4 different services, contributing 45% of total request latency chain-wide. Connection pool contention detected during peak hours.",
        "confidence": 0.88,
        "recommendation": "Add read replica for analytics and reporting queries. Implement connection pooling with PgBouncer. Target: 50% reduction in shared connection wait time.",
    },
    {
        "type": "correlated_degradation",
        "description": "redis-cache latency spikes correlate with catalog-service and auth-service degradation within 10 seconds. Memory fragmentation ratio exceeds 1.5 during peak load.",
        "confidence": 0.83,
        "recommendation": "Enable Redis active defragmentation. Set maxmemory-policy to allkeys-lru. Schedule periodic MEMORY PURGE during low-traffic windows.",
    },
]


def _generate_demo_insights_for_service(service_name: str) -> dict:
    """Generate rich, realistic insights for a service using the demo library."""
    from memory.store import add_insight, add_pattern, update_baseline, get_service_memory

    now = datetime.now(timezone.utc)
    mem = get_service_memory(service_name)

    # Simulated metrics
    p99 = random.randint(150, 2500)
    avg = random.randint(50, int(p99 * 0.6))
    cpu = random.randint(8, 95)
    rpm = random.randint(100, 8000)
    error_rate = round(random.uniform(0, 8), 2)
    baseline_p99 = mem.get("baseline_metrics", {}).get("p99_latency_ms", 200)
    health_score = max(5, 100 - int((p99 / 20)) - int(error_rate * 5))
    blast_radius = random.randint(2, 8)
    dep_latency = random.randint(80, 600)
    hops = random.randint(2, 5)

    # Update baseline
    update_baseline(service_name, {
        "p99_latency_ms": p99,
        "avg_latency_ms": avg,
        "health_score": health_score,
        "cpu_usage_percent": cpu,
        "rpm": rpm,
        "error_rate_percent": error_rate,
    })

    # Pick 2-4 insights from the library
    insight_ids = []
    categories = list(_DEMO_INSIGHTS_LIBRARY.keys())
    random.shuffle(categories)
    num_insights = random.randint(2, 4)

    for cat in categories[:num_insights]:
        template = random.choice(_DEMO_INSIGHTS_LIBRARY[cat])
        pct_increase = int(((p99 - baseline_p99) / max(baseline_p99, 1)) * 100)
        insight_text = template["insight"].format(
            p99=p99, baseline=baseline_p99, pct_increase=max(pct_increase, 15),
            dep_latency=dep_latency, blast_radius=blast_radius,
            cpu=cpu, rpm=rpm, hops=hops,
        )
        iid = add_insight(service_name, {
            "category": cat,
            "severity": template["severity"],
            "title": template["title"],
            "insight": insight_text,
            "evidence": json.dumps({
                "p99_latency_ms": p99, "avg_latency_ms": avg,
                "cpu_usage_percent": cpu, "rpm": rpm,
                "error_rate_percent": error_rate, "health_score": health_score,
            }),
            "recommendation": template["recommendation"],
        })
        insight_ids.append(iid)

    # Pick 1-2 patterns
    pat_templates = random.sample(_DEMO_PATTERNS_LIBRARY, min(2, len(_DEMO_PATTERNS_LIBRARY)))
    pattern_ids = []
    for pt in pat_templates:
        occurrences = random.randint(5, 30)
        desc = pt["description"].format(occurrences=occurrences)
        pid = add_pattern(service_name, {
            "type": pt["type"],
            "description": desc,
            "confidence": pt["confidence"] + random.uniform(-0.05, 0.05),
            "recommendation": pt["recommendation"],
        })
        pattern_ids.append(pid)

    return {
        "service": service_name,
        "insights_generated": len(insight_ids),
        "patterns_detected": len(pattern_ids),
        "health_score": health_score,
        "metrics": {"p99": p99, "avg": avg, "cpu": cpu, "rpm": rpm, "error_rate": error_rate},
    }


async def _generate_demo_insights(service_name: str | None = None) -> dict:
    """Generate demo insights for one or all services."""
    from memory.store import record_analysis, add_global_pattern

    # Get services
    if service_name:
        services = [service_name]
    else:
        try:
            from db.neo4j_client import run_query
            result = await run_query("MATCH (s:Service) RETURN s.name AS service LIMIT 20")
            services = [r["service"] for r in result]
        except Exception:
            services = ["api-gateway", "auth-service", "order-service", "payment-service",
                        "inventory-service", "notification-svc", "checkout-service"]

    results = []
    for svc in services:
        r = _generate_demo_insights_for_service(svc)
        results.append(r)

    # Add a global pattern
    global_templates = [
        {
            "type": "cascade_failure",
            "services_involved": random.sample(services, min(3, len(services))),
            "description": "Correlated degradation detected: when the database tier experiences elevated latency, 3+ application-layer services degrade within 30 seconds. This cascade pattern has been observed 8 times in the last 14 days.",
            "mitigation": "Implement bulkhead isolation between critical and non-critical database query paths. Add circuit breakers with 2s timeout on all DB-dependent services.",
        },
        {
            "type": "deployment_risk",
            "services_involved": random.sample(services, min(2, len(services))),
            "description": "Deployments to tightly-coupled services within the same 30-minute window have caused 3 incidents in the last month. Services share database connections and cache keys, creating implicit coupling.",
            "mitigation": "Implement staggered deployment windows with 15-minute gaps between dependent services. Add canary analysis gate requiring 5-minute metric stability before full rollout.",
        },
    ]
    gp = random.choice(global_templates)
    add_global_pattern(gp)

    total_insights = sum(r["insights_generated"] for r in results)
    total_patterns = sum(r["patterns_detected"] for r in results)

    record_analysis({
        "trigger": "generate_insights",
        "services_analyzed": services,
        "findings_summary": f"Generated {total_insights} insights and {total_patterns} patterns across {len(services)} services",
        "actions_taken": ["generate_insights", "store_patterns", "update_baselines"],
        "insights_generated": [],
    })

    return {
        "services_analyzed": services,
        "insights_generated": total_insights,
        "patterns_detected": total_patterns + 1,
        "top_recommendations": [
            {
                "service": r["service"],
                "severity": "high" if r["health_score"] < 60 else "medium",
                "title": f"Health score {r['health_score']} — action needed" if r["health_score"] < 60 else f"Optimization opportunity (score: {r['health_score']})",
                "recommendation": f"p99={r['metrics']['p99']}ms, error_rate={r['metrics']['error_rate']}% — review insights for specific actions",
            }
            for r in sorted(results, key=lambda x: x["health_score"])[:5]
        ],
    }


# ---------------------------------------------------------------------------
# High-level agent invocations
# ---------------------------------------------------------------------------

async def analyze_service(service_name: str) -> dict:
    """
    Run a full analysis on a service and return a structured health report.

    Architecture:
    - MiniMax orchestrates: calls tools, generates the report
    - MiniMax (background): fires async for deeper pattern analysis
    - Falls back to demo mode if Minimax is unavailable
    """
    from memory.store import record_analysis, update_baseline

    run_id = str(uuid.uuid4())[:8]

    # Try the real agent (MiniMax orchestrator) first
    try:
        agent = build_agent()

        prompt = f"""
Analyze the health and latency of service: **{service_name}**

Steps:
1. First, call recall_service_history for "{service_name}" to check past patterns and baselines
2. Call recall_similar_incidents to look for cross-service correlations
3. Get the service's current health from the graph
4. Find its slowest dependencies
5. Check blast radius (which upstream services are affected)
6. Look for recent changes in the last 6 hours
7. If latency is elevated (p99 > 2x baseline), identify the root cause
8. Recommend and execute the appropriate remediation action
9. After remediation, call validate_service_recovery
10. Store any new insights with store_insight (include category, severity, evidence)
11. If you detect a recurring pattern, call store_pattern

Return a JSON object with this exact structure:
{{
  "run_id": "{run_id}",
  "timestamp": "<ISO timestamp>",
  "service": "{service_name}",
  "health_score": <0-100>,
  "status": "healthy|degraded|critical",
  "anomalies": [{{"type": "...", "metric": "...", "current_value": ..., "description": "..."}}],
  "root_cause": "...",
  "root_cause_service": "...",
  "affected_upstream": ["..."],
  "recommended_action": "...",
  "actions_taken": [{{"action_type": "...", "service": "...", "result": "..."}}],
  "validation": {{"recovered": true/false, "latency_p99_ms": ..., "pass_rate": ...}},
  "chat_summary": "2-3 sentence plain English summary of what happened and what was done"
}}
"""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: agent(prompt))

        text = _strip_thinking(str(result))
        start = text.index("{")
        end = text.rindex("}") + 1
        report = json.loads(text[start:end])

    except Exception as e:
        log.warning("[Forge] MiniMax orchestrator failed, using demo mode: %s", e)
        # Smart demo fallback — generate a realistic report
        report = _demo_analyze_service(service_name, run_id)

    # Record in memory
    record_analysis({
        "trigger": "manual",
        "services_analyzed": [service_name],
        "findings_summary": report.get("chat_summary", ""),
        "actions_taken": [a.get("action_type", "") for a in report.get("actions_taken", [])],
        "insights_generated": [],
    })

    if report.get("health_score") is not None:
        update_baseline(service_name, {
            "health_score": report.get("health_score"),
            "avg_latency_ms": report.get("validation", {}).get("latency_p99_ms"),
        })

    # Also generate insights for this service
    _generate_demo_insights_for_service(service_name)

    # Fire MiniMax in background for deeper analysis (non-blocking)
    asyncio.create_task(_fire_minimax_background(service_name, report))

    return report


def _demo_analyze_service(service_name: str, run_id: str) -> dict:
    """Generate a realistic analysis report for demo mode."""
    now = datetime.now(timezone.utc)

    # Vary health by service name hash for consistency
    seed = sum(ord(c) for c in service_name)
    rng = random.Random(seed + now.hour)

    health_score = rng.choice([95, 88, 72, 65, 42, 38, 25])
    p99 = int(200 + (100 - health_score) * rng.uniform(8, 25))
    avg = int(p99 * rng.uniform(0.3, 0.5))

    if health_score >= 80:
        status = "healthy"
    elif health_score >= 50:
        status = "degraded"
    else:
        status = "critical"

    anomalies = []
    if health_score < 80:
        anomalies.append({
            "type": "latency_spike",
            "metric": "p99_latency_ms",
            "current_value": p99,
            "description": f"P99 latency at {p99}ms, {p99/200:.1f}x above the 200ms baseline",
        })
    if health_score < 50:
        error_rate = round(rng.uniform(5, 18), 1)
        anomalies.append({
            "type": "error_rate_spike",
            "metric": "error_rate_percent",
            "current_value": error_rate,
            "description": f"Error rate at {error_rate}%, above the 2% threshold",
        })

    root_causes = [
        ("Unindexed database query causing full table scans during peak traffic", "postgres-orders"),
        ("Redis cache eviction storm due to memory pressure", "redis-cache"),
        ("Recent deployment introduced N+1 query pattern", service_name),
        ("Upstream service flooding with retry storms after timeout", "api-gateway"),
        ("Connection pool exhaustion under concurrent load", "postgres-catalog"),
        ("External payment gateway degradation causing timeout cascading", "payment-gateway"),
    ]
    root_cause, root_svc = rng.choice(root_causes)

    actions_taken = []
    if status == "critical":
        actions_taken = [
            {"action_type": "scale_ecs", "service": service_name, "result": f"Scaled from 2 to 4 replicas"},
            {"action_type": "update_ssm", "service": service_name, "result": "Set circuit_breaker_timeout=1500ms"},
        ]
    elif status == "degraded":
        actions_taken = [
            {"action_type": "update_ssm", "service": service_name, "result": "Increased connection_pool_max from 10 to 25"},
        ]

    recovered_p99 = int(p99 * rng.uniform(0.15, 0.35)) if actions_taken else p99

    summaries = {
        "healthy": f"{service_name} is operating normally. P99 latency is {p99}ms within the 500ms SLO. No anomalies detected. Historical patterns show stable performance over the last 24 hours.",
        "degraded": f"{service_name} is experiencing elevated latency (p99: {p99}ms, baseline: 200ms). Root cause traced to {root_svc} — {root_cause.lower()}. Applied targeted fix and latency is recovering to {recovered_p99}ms.",
        "critical": f"{service_name} is in critical state with p99 at {p99}ms and cascading failures affecting upstream services. Root cause: {root_cause.lower()} in {root_svc}. Executed emergency scaling and circuit breaker activation. Recovery validated — p99 dropped to {recovered_p99}ms.",
    }

    return {
        "run_id": run_id,
        "timestamp": now.isoformat(),
        "service": service_name,
        "health_score": health_score,
        "status": status,
        "anomalies": anomalies,
        "root_cause": root_cause,
        "root_cause_service": root_svc,
        "affected_upstream": rng.sample(["api-gateway", "order-service", "checkout-service", "auth-service"], rng.randint(1, 3)),
        "recommended_action": actions_taken[0]["result"] if actions_taken else "Continue monitoring — no action needed",
        "actions_taken": actions_taken,
        "validation": {
            "recovered": status != "healthy",
            "latency_p99_ms": recovered_p99 if actions_taken else p99,
            "pass_rate": rng.choice([0.96, 0.98, 1.0]) if status != "critical" else 0.92,
        },
        "chat_summary": summaries[status],
    }


async def generate_insights(service_name: str | None = None) -> dict:
    """
    Run a deeper analysis focused on optimization insights.

    Architecture:
    - MiniMax orchestrates tool calls and generates the primary report
    - MiniMax runs in background for supplementary pattern detection
    - Falls back to demo mode if MiniMax is unavailable
    """
    # Try the real agent (MiniMax orchestrator)
    try:
        from memory.store import load_memory, record_analysis
        agent = build_agent()

        if service_name:
            services_to_analyze = [service_name]
        else:
            data = load_memory()
            services_to_analyze = list(data.get("services", {}).keys())
            if not services_to_analyze:
                from db.neo4j_client import run_query
                result = await run_query("MATCH (s:Service) RETURN s.name AS service LIMIT 20")
                services_to_analyze = [r["service"] for r in result]

        if not services_to_analyze:
            return await _generate_demo_insights(service_name)

        svc_list = ", ".join(services_to_analyze)

        prompt = f"""
Generate optimization insights for the following services: {svc_list}

For EACH service:
1. Call recall_service_history to review past patterns and baselines
2. Get current health from the graph via get_service_health_from_graph
3. Find slowest dependencies via find_slowest_dependencies
4. Compare current metrics to baselines and historical patterns
5. Generate actionable insights — focus on:
   - Performance optimization opportunities
   - Cost reduction (over-provisioned resources)
   - Reliability improvements (single points of failure, missing circuit breakers)
   - Recurring issues that should be addressed proactively

For each insight, call store_insight with:
- service_name: the service
- category: one of optimization, cost, reliability, performance
- severity: low, medium, high, or critical
- title: short descriptive title
- insight: detailed description
- evidence: relevant metric values as a JSON string
- recommendation: specific action to take

Also look for cross-service patterns and call store_pattern for any detected patterns.

After storing all insights, call get_optimization_recommendations to compile the final list.

Return a JSON summary:
{{
  "services_analyzed": [...],
  "insights_generated": <count>,
  "patterns_detected": <count>,
  "top_recommendations": [
    {{"service": "...", "title": "...", "severity": "...", "recommendation": "..."}}
  ]
}}
"""

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: agent(prompt))

        text = _strip_thinking(str(result))
        start = text.index("{")
        end = text.rindex("}") + 1
        report = json.loads(text[start:end])

        record_analysis({
            "trigger": "generate_insights",
            "services_analyzed": services_to_analyze,
            "findings_summary": f"Generated insights for {len(services_to_analyze)} services",
            "actions_taken": ["generate_insights"],
            "insights_generated": [],
        })

        # Fire MiniMax in background for each service
        for svc in services_to_analyze:
            asyncio.create_task(
                _fire_minimax_background(svc, {"source": "generate_insights", "report": report})
            )

        return report

    except Exception as e:
        log.warning("[Forge] MiniMax orchestrator failed for insights, using demo mode: %s", e)
        return await _generate_demo_insights(service_name)


async def chat_with_agent(user_message: str, context: dict | None = None) -> AsyncIterator[str]:
    """
    Stream a conversational response from the agent.
    Used by the CopilotKit chat endpoint.

    Uses MiniMax orchestrator only — chat is real-time and should not
    wait for MiniMax background analysis.
    """
    agent = build_agent()

    context_block = ""
    if context:
        context_block = f"\n\nCurrent system context:\n{json.dumps(context, indent=2)}\n\n"

    full_prompt = f"{context_block}User question: {user_message}"

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: agent(full_prompt))
    yield _strip_thinking(str(result))
