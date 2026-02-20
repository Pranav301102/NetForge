"""Graph endpoints — serve Neo4j data to the React force-graph visualization."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from db.neo4j_client import run_query

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/")
async def get_full_graph():
    """
    Return the complete service dependency graph in a format compatible with
    react-force-graph-2d: { nodes: [...], links: [...] }
    Streams the response in chunks to avoid ECONNRESET on large graphs.
    """
    nodes_raw = await run_query(
        """
        MATCH (s:Service)
        RETURN s.name AS id,
               s.name AS label,
               s.type AS type,
               s.team AS team,
               s.criticality AS criticality,
               s.health_score AS health_score,
               s.avg_latency_ms AS avg_latency_ms,
               s.p99_latency_ms AS p99_latency_ms
        """
    )

    links_raw = await run_query(
        """
        MATCH (a:Service)-[r:CALLS]->(b:Service)
        RETURN a.name AS source,
               b.name AS target,
               r.avg_latency_ms AS avg_latency_ms,
               r.p99_latency_ms AS p99_latency_ms,
               r.requests_per_min AS rpm
        """
    )

    # Enrich nodes with color based on health score
    nodes = []
    for n in nodes_raw:
        score = n.get("health_score", 100)
        if score >= 80:
            color = "#22c55e"
        elif score >= 50:
            color = "#f59e0b"
        else:
            color = "#ef4444"
        nodes.append({
            **n,
            "color": color,
            "val": 8 if n.get("criticality") == "critical" else 5,
        })

    async def stream():
        yield '{"nodes":['
        for i, node in enumerate(nodes):
            if i > 0:
                yield ","
            yield json.dumps(node)
        yield '],"links":['
        for i, link in enumerate(links_raw):
            if i > 0:
                yield ","
            yield json.dumps(link)
        yield "]}"

    return StreamingResponse(
        stream(),
        media_type="application/json",
        headers={"X-Accel-Buffering": "no"},
    )


@router.get("/service/{service_name}")
async def get_service_subgraph(service_name: str, hops: int = 2):
    """
    Return the ego-graph (neighbourhood) for a specific service —
    useful for zooming into a problematic service in the UI.
    """
    nodes_raw = await run_query(
        f"""
        MATCH (center:Service {{name: $name}})
        OPTIONAL MATCH (center)-[:CALLS*1..{hops}]-(neighbor:Service)
        WITH collect(DISTINCT center) + collect(DISTINCT neighbor) AS all_nodes
        UNWIND all_nodes AS s
        RETURN DISTINCT
               s.name AS id,
               s.name AS label,
               s.type AS type,
               s.team AS team,
               s.criticality AS criticality,
               s.health_score AS health_score,
               s.avg_latency_ms AS avg_latency_ms,
               s.p99_latency_ms AS p99_latency_ms
        """,
        {"name": service_name},
    )

    if not nodes_raw:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not found")

    node_ids = {n["id"] for n in nodes_raw}

    links_raw = await run_query(
        """
        MATCH (a:Service)-[r:CALLS]->(b:Service)
        WHERE a.name IN $ids AND b.name IN $ids
        RETURN a.name AS source,
               b.name AS target,
               r.avg_latency_ms AS avg_latency_ms,
               r.p99_latency_ms AS p99_latency_ms,
               r.requests_per_min AS rpm
        """,
        {"ids": list(node_ids)},
    )

    return {"nodes": nodes_raw, "links": links_raw, "center": service_name}


@router.get("/deployments/recent")
async def get_recent_deployments(hours: int = 12):
    """Return recent deployments across all services for the timeline."""
    results = await run_query(
        """
        MATCH (s:Service)-[:HAD_DEPLOYMENT]->(d:Deployment)
        WHERE datetime(d.deployed_at) >= datetime() - duration({hours: $hours})
        RETURN s.name AS service,
               d.id AS deployment_id,
               d.version AS version,
               d.deployed_at AS deployed_at,
               d.status AS status,
               d.deployed_by AS deployed_by
        ORDER BY d.deployed_at DESC
        """,
        {"hours": hours},
    )
    return {"deployments": results, "window_hours": hours}
