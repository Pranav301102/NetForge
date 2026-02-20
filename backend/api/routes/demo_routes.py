"""Demo/testing routes — seed data and serve the demo HTML page."""
from __future__ import annotations

from fastapi import APIRouter

from db.seed import seed, SERVICES, CALL_EDGES, DEPLOYMENTS

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/seed")
async def seed_database():
    """
    Seed Neo4j with the 24-service demo topology.
    Safe to call multiple times (uses MERGE — idempotent).
    """
    await seed()
    return {
        "seeded": True,
        "services": len(SERVICES),
        "edges": len(CALL_EDGES),
        "deployments": len(DEPLOYMENTS),
    }
