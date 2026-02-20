"""Remediation action history endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from agent.tools.aws_tools import get_action_log

router = APIRouter(prefix="/api/actions", tags=["actions"])


@router.get("/")
async def list_actions(limit: int = 50):
    """Return the most recent remediation actions taken by the agent."""
    log = get_action_log()
    return {"actions": log[:limit], "total": len(log)}


@router.delete("/")
async def clear_actions():
    """Clear action history (for demo resets)."""
    from agent.tools.aws_tools import _action_log
    _action_log.clear()
    return {"cleared": True}
