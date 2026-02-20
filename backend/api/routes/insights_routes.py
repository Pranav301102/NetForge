"""
Insights API — memory-backed endpoints for the Forge insights engine.

Replaces the original mock-data routes with real persistent storage
backed by the JSON memory store.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from memory.store import (
    get_all_insights,
    get_all_patterns,
    get_recommendations,
    get_service_memory,
    update_insight_status,
)

router = APIRouter(prefix="/api/insights", tags=["insights"])


class GenerateRequest(BaseModel):
    service_name: str | None = None


class StatusUpdate(BaseModel):
    status: str  # "acknowledged" or "resolved"


# ---------------------------------------------------------------------------
# List all insights (with optional filters)
# ---------------------------------------------------------------------------

@router.get("/")
async def list_insights(
    status: str | None = None,
    severity: str | None = None,
    category: str | None = None,
):
    """Return all insights across all services, optionally filtered."""
    insights = get_all_insights(status=status)
    if severity:
        insights = [i for i in insights if i.get("severity") == severity]
    if category:
        insights = [i for i in insights if i.get("category") == category]
    return {"insights": insights, "count": len(insights)}


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

@router.get("/patterns")
async def list_patterns():
    """Return all detected patterns (service-level + global)."""
    patterns = get_all_patterns()
    return {"patterns": patterns, "count": len(patterns)}


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

@router.get("/recommendations")
async def list_recommendations():
    """Return all open high/critical insights with recommendations."""
    recs = get_recommendations()
    return {"recommendations": recs, "count": len(recs)}


# ---------------------------------------------------------------------------
# Generate insights (triggers the agent)
# ---------------------------------------------------------------------------

@router.post("/generate")
async def generate_insights_endpoint(body: GenerateRequest):
    """
    Trigger the agent to generate fresh insights for a service (or all services).
    This is an async operation — insights are stored in memory and returned.
    """
    from agent.agent import generate_insights

    try:
        result = await generate_insights(body.service_name)
        return {"status": "ok", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Update insight status
# ---------------------------------------------------------------------------

@router.patch("/{insight_id}")
async def patch_insight(insight_id: str, body: StatusUpdate):
    """Mark an insight as acknowledged or resolved."""
    if body.status not in ("acknowledged", "resolved", "open"):
        raise HTTPException(status_code=400, detail="Invalid status. Use: open, acknowledged, resolved")
    found = update_insight_status(insight_id, body.status)
    if not found:
        raise HTTPException(status_code=404, detail=f"Insight {insight_id} not found")
    return {"status": "updated", "insight_id": insight_id, "new_status": body.status}


# ---------------------------------------------------------------------------
# Service-specific insights (must come LAST to avoid route conflicts)
# ---------------------------------------------------------------------------

@router.get("/{service_name}")
async def get_service_insights(service_name: str):
    """
    Get insights + patterns + baseline for a specific service.
    Falls back to the memory store data.
    """
    mem = get_service_memory(service_name)
    return {
        "service": service_name,
        "baseline_metrics": mem.get("baseline_metrics", {}),
        "patterns": mem.get("patterns", []),
        "insights": mem.get("insights", []),
        "pattern_count": len(mem.get("patterns", [])),
        "insight_count": len(mem.get("insights", [])),
    }
