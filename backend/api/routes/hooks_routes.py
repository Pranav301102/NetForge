"""
Webhook endpoints for external system integrations.

POST /api/hooks/deploy         — triggers agent analysis on a service when deployed
POST /api/hooks/datadog-sync   — fetches live metrics via Datadog REST and updates Neo4j
POST /api/hooks/scale          — scale-up or scale-down a service + TestSprite network
                                 stability validation (pre/post)
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from agent.agent import analyze_service, generate_insights
from db.neo4j_client import run_query
from memory.store import add_insight, update_baseline

router = APIRouter(prefix="/api/hooks", tags=["hooks"])


class DeployPayload(BaseModel):
    service: str
    version: str | None = None
    status: str | None = "success"


class DatadogSyncPayload(BaseModel):
    services: list[str] | None = None  # Sync all if None


class ScalePayload(BaseModel):
    service: str
    cluster: str = "forge-prod-cluster"
    direction: str = "up"           # "up" or "down"
    instance_count: int = 4         # desired count after scale
    reason: str = "triggered via hook"
    run_stability_test: bool = True
    stabilization_wait_seconds: int = 30


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/hooks/deploy
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/deploy")
async def handle_deploy_hook(payload: DeployPayload, background_tasks: BackgroundTasks):
    """
    Called by CodeDeploy/ECS/Lambda after a deployment.
    Logs the deployment to Neo4j and kicks off async agent analysis.
    """
    try:
        # Record deployment in Neo4j
        await run_query(
            """
            MATCH (s:Service {name: $service})
            CREATE (d:Deployment {
                id: randomUUID(),
                version: $version,
                status: $status,
                deployed_at: toString(datetime()),
                deployed_by: \"webhook\"
            })
            CREATE (s)-[:HAD_DEPLOYMENT]->(d)
            """,
            {"service": payload.service, "version": payload.version, "status": payload.status},
        )

        # Kick off async agent analysis + insight generation
        background_tasks.add_task(analyze_service, payload.service)
        background_tasks.add_task(generate_insights, payload.service)

        return {
            "status":  "accepted",
            "message": f"Deployment logged and analysis started for {payload.service}",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/hooks/datadog-sync
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/datadog-sync")
async def sync_datadog_metrics(payload: DatadogSyncPayload):
    """
    Pulls real-time container/infrastructure metrics from Datadog REST API
    and writes them back into Neo4j so the dependency graph stays fresh.

    Uses direct REST (not MCP) for reliability — the MCP path requires a
    running npx subprocess which is less suitable for synchronous webhooks.
    """
    from agent.tools.datadog_tools import fetch_live_metrics_for_service

    try:
        # Determine which services to sync
        if not payload.services:
            services_res = await run_query("MATCH (s:Service) RETURN s.name AS service")
            services_to_sync = [r["service"] for r in services_res]
        else:
            services_to_sync = payload.services

        if not services_to_sync:
            return {"status": "ok", "services_updated": 0, "message": "No services in graph yet"}

        updated = 0
        anomalies_detected = []

        for svc in services_to_sync:
            try:
                metrics = fetch_live_metrics_for_service(svc)
            except Exception as e:
                # Gracefully skip this service rather than abort the whole sync
                continue

            p99   = metrics["p99_latency_ms"]
            avg   = metrics["avg_latency_ms"]
            score = metrics["health_score"]
            cpu   = metrics.get("cpu_usage_percent") or 0
            mem   = metrics.get("mem_usage_percent") or 0

            # Write back to Neo4j
            await run_query(
                """
                MATCH (s:Service {name: $service})
                SET s.p99_latency_ms    = $p99,
                    s.avg_latency_ms    = $avg,
                    s.health_score      = $score,
                    s.cpu_usage_percent = $cpu,
                    s.mem_usage_percent = $mem,
                    s.data_source       = \"datadog_live\",
                    s.updated_at        = toString(datetime())
                """,
                {
                    "service": svc,
                    "p99":     p99,
                    "avg":     avg,
                    "score":   score,
                    "cpu":     cpu,
                    "mem":     mem,
                },
            )

            # Store insight if anomalous
            if score < 60 or p99 > 1000:
                severity = "high" if p99 > 1000 or score < 40 else "medium"
                add_insight(svc, {
                    "category":       "performance",
                    "severity":       severity,
                    "title":          f"Elevated p99 latency from Datadog live sync ({p99}ms)",
                    "insight":        (
                        f"Live Datadog sync measured p99={p99}ms, avg={avg}ms "
                        f"for {svc}. Health score: {score}. "
                        f"CPU: {cpu}%, Mem: {mem}%."
                    ),
                    "evidence":       f'{{"p99_latency_ms": {p99}, "avg_latency_ms": {avg}, "health_score": {score}, "cpu": {cpu}, "mem": {mem}}}',
                    "recommendation": "Investigate slow dependencies and consider scaling or circuit breakers.",
                })
                anomalies_detected.append({"service": svc, "p99": p99, "score": score})

            update_baseline(svc, {
                "p99_latency_ms": p99,
                "avg_latency_ms": avg,
                "health_score":   score,
            })
            updated += 1

        return {
            "status":            "success",
            "data_source":       "datadog_live",
            "services_updated":  updated,
            "anomalies_detected": len(anomalies_detected),
            "anomalies":         anomalies_detected,
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/hooks/scale
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/scale")
async def scale_and_validate(payload: ScalePayload):
    """
    Full scale-up or scale-down pipeline:

      1. Get the current replica count from ECS (or graph for demo)
      2. (Optional) Run Phase 1 TestSprite network stability test (pre-scale baseline)
      3. Execute scale_ecs_service
      4. Wait for stabilization
      5. (Optional) Run Phase 2 TestSprite network stability test (post-scale)
      6. Update Neo4j with new scale state
      7. Return combined result including scale + test verdicts

    This implements the network-stability-testing-on-scale-events requirement.
    """
    from agent.tools.aws_tools import scale_ecs_service, get_action_log
    from agent.tools.testsprite import validate_scale_stability
    import json

    try:
        # Step 1: Determine current replica count from the action log or default
        action_log = get_action_log()
        previous_scale_actions = [
            a for a in action_log
            if a["action_type"] == "scale_ecs" and a["service"] == payload.service
        ]
        instance_before = 2  # default
        if previous_scale_actions:
            last = previous_scale_actions[0]
            instance_before = last["result"].get("new_desired_count", 2)

        # Step 2: Scale the service
        scale_result_raw = await scale_ecs_service(
            cluster=payload.cluster,
            service=payload.service,
            desired_count=payload.instance_count,
            reason=payload.reason,
        )
        scale_result = json.loads(scale_result_raw)

        # Step 3: Optional network stability test (pre/post)
        stability_result = None
        if payload.run_stability_test:
            stability_raw = await validate_scale_stability(
                service_name=payload.service,
                scale_direction=payload.direction,
                instance_count_before=instance_before,
                instance_count_after=payload.instance_count,
                stabilization_wait_seconds=payload.stabilization_wait_seconds,
            )
            stability_result = json.loads(stability_raw)

        # Step 4: Update Neo4j with new scale state
        await run_query(
            """
            MATCH (s:Service {name: $service})
            SET s.replica_count = $count,
                s.last_scaled_at = toString(datetime()),
                s.scale_direction = $direction
            """,
            {
                "service":   payload.service,
                "count":     payload.instance_count,
                "direction": payload.direction,
            },
        )

        # Step 5: If stability test failed, flag a remediation insight
        if stability_result and not stability_result.get("network_stable"):
            add_insight(payload.service, {
                "category":       "reliability",
                "severity":       "high",
                "title":          f"Network instability detected after scale-{payload.direction}",
                "insight":        (
                    f"After scaling {payload.service} from {instance_before} to "
                    f"{payload.instance_count} replicas (scale-{payload.direction}), "
                    f"TestSprite network stability tests detected a regression. "
                    f"Pass rate delta: {stability_result['delta']['pass_rate_pct']}%. "
                    f"P99 delta: {stability_result['delta']['latency_ms']}ms."
                ),
                "evidence":       json.dumps({
                    "pre_scale":  stability_result.get("phase_1_pre_scale"),
                    "post_scale": stability_result.get("phase_2_post_scale"),
                }),
                "recommendation": (
                    "Consider reverting the scale event or investigating service discovery "
                    "and load balancer health check configuration."
                ),
            })

        return {
            "status":          "success",
            "service":         payload.service,
            "direction":       payload.direction,
            "instance_before": instance_before,
            "instance_after":  payload.instance_count,
            "scale_result":    scale_result,
            "stability_test":  stability_result,
            "network_stable":  stability_result["network_stable"] if stability_result else None,
            "verdict":         stability_result["verdict"] if stability_result else "Stability test skipped",
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
