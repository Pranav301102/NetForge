"""
Network Testing Strategy API.

Exposes the network_tester agent to the frontend:
  GET  /api/network-test/strategies  — list strategies derived from memory
  POST /api/network-test/run         — execute all (or specific) strategies
  GET  /api/network-test/results     — most recent test report
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.network_tester import generate_strategies, run_network_tests
from memory.store import get_all_insights, get_all_patterns

router = APIRouter(prefix="/api/network-test", tags=["network-test"])

# In-memory cache of the most recent report (no DB needed for demo)
_last_report: dict | None = None


class RunRequest(BaseModel):
    strategy_ids: Optional[list[str]] = None  # None = run all


@router.get("/strategies")
async def list_strategies():
    """Return the test strategies the agent would run based on current memory."""
    insights = get_all_insights()
    patterns = get_all_patterns()
    strategies = generate_strategies(insights, patterns)
    return {
        "strategies": [
            {
                "id": s.id,
                "name": s.name,
                "type": s.type,
                "description": s.description,
                "target": s.target,
                "severity": s.severity,
                "derived_from": s.derived_from,
                "concurrency": s.concurrency,
                "samples": s.samples,
            }
            for s in strategies
        ],
        "count": len(strategies),
        "derived_from_insights": len(insights),
        "derived_from_patterns": len(patterns),
    }


@router.post("/run")
async def run_tests(body: RunRequest):
    """
    Execute the network test strategies and stream results back as JSON chunks.
    Stores the final report in memory for /results.
    """
    global _last_report

    async def stream():
        global _last_report
        report = await run_network_tests(body.strategy_ids)
        _last_report = report.to_dict()
        payload = json.dumps(_last_report)
        chunk_size = 4096
        for i in range(0, len(payload), chunk_size):
            yield payload[i:i + chunk_size]

    return StreamingResponse(
        stream(),
        media_type="application/json",
        headers={"X-Accel-Buffering": "no"},
    )


@router.get("/results")
async def get_results():
    """Return the most recent test report, or an empty placeholder."""
    if _last_report is None:
        return {
            "report_id": None,
            "overall_status": "not_run",
            "strategies_run": 0,
            "strategies_passed": 0,
            "strategies_failed": 0,
            "duration_ms": 0,
            "strategy_results": [],
            "recommendations": [],
            "message": "No tests run yet. POST to /api/network-test/run to start.",
        }
    return _last_report
