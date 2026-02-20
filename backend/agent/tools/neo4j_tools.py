"""
Neo4j graph tools for the Strands agent.
These give the agent the ability to query service topology and deployment history.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from strands import tool

from db.neo4j_client import run_query


@tool
async def get_service_dependencies(service_name: str) -> str:
    """
    Get all upstream and downstream service dependencies for a given service.
    Returns which services call this service and which services this service calls,
    including latency metrics on each edge. Use this to understand blast radius.

    Args:
        service_name: Name of the service to inspect (e.g., "payment-service")

    Returns:
        JSON string with upstream callers, downstream callees, and latency metrics
    """
    # Services that call this service (upstream — they are affected if this is slow)
    upstream = await run_query(
        """
        MATCH (caller:Service)-[r:CALLS]->(target:Service {name: $name})
        RETURN caller.name AS service,
               r.avg_latency_ms AS avg_latency_ms,
               r.p99_latency_ms AS p99_latency_ms,
               r.requests_per_min AS rpm
        ORDER BY r.requests_per_min DESC
        """,
        {"name": service_name},
    )

    # Services this service calls (downstream dependencies)
    downstream = await run_query(
        """
        MATCH (target:Service {name: $name})-[r:CALLS]->(dep:Service)
        RETURN dep.name AS service,
               dep.type AS type,
               r.avg_latency_ms AS avg_latency_ms,
               r.p99_latency_ms AS p99_latency_ms,
               r.requests_per_min AS rpm
        ORDER BY r.avg_latency_ms DESC
        """,
        {"name": service_name},
    )

    return json.dumps({
        "service": service_name,
        "upstream_callers": upstream,    # services that will be affected
        "downstream_dependencies": downstream,  # what this service depends on
    }, default=str)


@tool
async def find_recent_changes(service_name: str, hours: int = 6) -> str:
    """
    Find recent deployments and changes to a service and its direct dependencies
    within a given time window. Critical for root cause analysis — correlate
    anomalies with recent changes.

    Args:
        service_name: Name of the service to check
        hours: How many hours back to look (default 6)

    Returns:
        JSON list of recent changes sorted by recency
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    # Deployments to this service AND its direct dependencies
    results = await run_query(
        """
        MATCH (s:Service)-[:HAD_DEPLOYMENT]->(d:Deployment)
        WHERE (s.name = $name OR EXISTS {
            MATCH (target:Service {name: $name})-[:CALLS]->(s)
        })
        AND d.deployed_at >= $since
        RETURN s.name AS service,
               d.version AS version,
               d.deployed_at AS deployed_at,
               d.status AS status,
               d.deployed_by AS deployed_by,
               d.id AS deployment_id
        ORDER BY d.deployed_at DESC
        """,
        {"name": service_name, "since": since},
    )

    return json.dumps({"changes": results, "window_hours": hours}, default=str)


@tool
async def get_blast_radius(service_name: str, max_hops: int = 3) -> str:
    """
    Compute the full blast radius — all services that transitively depend on
    the given service, up to max_hops away. Shows which services will experience
    latency or failures if the target service degrades.

    Args:
        service_name: The degraded/problematic service
        max_hops: How many hops away to traverse (default 3)

    Returns:
        JSON with all affected services, hop distance, and their criticality
    """
    results = await run_query(
        """
        MATCH path = (caller:Service)-[:CALLS*1..$hops]->(target:Service {name: $name})
        WITH caller, length(path) AS hops
        RETURN DISTINCT
               caller.name AS service,
               caller.criticality AS criticality,
               caller.team AS team,
               hops
        ORDER BY hops ASC, caller.criticality DESC
        """,
        {"name": service_name, "hops": max_hops},
    )

    return json.dumps({
        "root_service": service_name,
        "affected_upstream": results,
        "total_affected": len(results),
    }, default=str)


@tool
async def get_service_health_from_graph(service_name: str) -> str:
    """
    Retrieve the current health score, latency metrics, and node properties
    for a service directly from the Neo4j graph. Use when you need a quick
    snapshot of a service's recorded state.

    Args:
        service_name: Name of the service

    Returns:
        JSON with health_score, latency metrics, type, team, criticality
    """
    results = await run_query(
        """
        MATCH (s:Service {name: $name})
        RETURN s.name AS name,
               s.health_score AS health_score,
               s.avg_latency_ms AS avg_latency_ms,
               s.p99_latency_ms AS p99_latency_ms,
               s.type AS type,
               s.team AS team,
               s.criticality AS criticality,
               s.updated_at AS updated_at
        """,
        {"name": service_name},
    )
    if not results:
        return json.dumps({"error": f"Service '{service_name}' not found in graph"})
    return json.dumps(results[0], default=str)


@tool
async def find_slowest_dependencies(service_name: str) -> str:
    """
    Find the slowest downstream dependencies of a service, ranked by p99 latency.
    Useful for identifying which external or internal service is the root bottleneck.

    Args:
        service_name: Service to inspect

    Returns:
        JSON list of dependencies sorted by p99 latency descending
    """
    results = await run_query(
        """
        MATCH (s:Service {name: $name})-[r:CALLS]->(dep:Service)
        RETURN dep.name AS dependency,
               dep.type AS type,
               r.avg_latency_ms AS avg_latency_ms,
               r.p99_latency_ms AS p99_latency_ms,
               r.requests_per_min AS rpm
        ORDER BY r.p99_latency_ms DESC
        """,
        {"name": service_name},
    )
    return json.dumps({"service": service_name, "dependencies": results}, default=str)
