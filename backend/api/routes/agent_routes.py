"""
Agent invocation routes.

POST /api/agent/analyze    — full analysis of a service
POST /api/agent/chat       — conversational SSE stream (CopilotKit backend)
GET  /api/agent/health     — quick poll of all service health scores
"""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.agent import analyze_service, chat_with_agent
from db.neo4j_client import run_query

router = APIRouter(prefix="/api/agent", tags=["agent"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    service: str
    trigger: str = "manual"  # "manual" | "alert" | "scheduled"


class ChatRequest(BaseModel):
    message: str
    context: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """
    Run the full Strands agent analysis loop on a service.
    Returns structured JSON: {service, health_score, anomalies[], recommended_action, ...}
    """
    try:
        result = await analyze_service(req.service)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/chat")
async def chat(req: ChatRequest):
    """
    Streaming SSE endpoint for CopilotKit chat integration.
    The frontend connects here via the CopilotKit runtimeUrl.

    Yields Server-Sent Events with text chunks from the agent.
    """
    async def event_stream():
        try:
            async for chunk in chat_with_agent(req.message, req.context):
                data = json.dumps({"type": "text", "content": chunk})
                yield f"data: {data}\n\n"
        except Exception as exc:
            error_data = json.dumps({"type": "error", "content": str(exc)})
            yield f"data: {error_data}\n\n"
        finally:
            yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/activity")
async def get_activity(since_id: int = 0, limit: int = 50):
    """
    Live activity feed — returns recent agent tool calls, analysis steps,
    and insights stored. Frontend polls this every 3s on the Agent tab.
    """
    from agent.activity_log import get_recent_activity
    entries = get_recent_activity(since_id=since_id, limit=limit)
    return {"activity": entries, "count": len(entries)}


@router.get("/health")
async def get_all_health():
    """
    Fast poll endpoint — returns health scores for all services.
    Frontend polls this every 5s to update node colors in the graph.
    """
    services = await run_query(
        """
        MATCH (s:Service)
        RETURN s.name AS service,
               s.health_score AS health_score,
               s.avg_latency_ms AS avg_latency_ms,
               s.p99_latency_ms AS p99_latency_ms,
               s.updated_at AS updated_at
        ORDER BY s.health_score ASC
        """
    )
    return {"services": services, "timestamp": time.time()}


@router.post("/simulate/degrade")
async def simulate_degradation(service: str = "payment-service"):
    """
    Demo helper: artificially degrade a service in Neo4j to trigger
    the agent analysis flow. Resets after next analysis.
    """
    await run_query(
        """
        MATCH (s:Service {name: $name})
        SET s.health_score = 32,
            s.avg_latency_ms = 1400,
            s.p99_latency_ms = 4200,
            s.updated_at = toString(datetime())
        """,
        {"name": service},
    )
    return {"degraded": service, "health_score": 32, "p99_latency_ms": 4200}


@router.post("/simulate/recover")
async def simulate_recovery(service: str = "payment-service"):
    """Demo helper: reset a service back to healthy state."""
    await run_query(
        """
        MATCH (s:Service {name: $name})
        SET s.health_score = 98,
            s.avg_latency_ms = 80,
            s.p99_latency_ms = 250,
            s.updated_at = toString(datetime())
        """,
        {"name": service},
    )
    return {"recovered": service, "health_score": 98, "p99_latency_ms": 250}
