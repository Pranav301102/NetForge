"""
Cluster management API — exposes the MAPE-K coordinator to the frontend.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cluster.coordinator import get_coordinator

router = APIRouter(prefix="/api/cluster", tags=["cluster"])


class EnqueueRequest(BaseModel):
    service_name: str
    task_type: str = "analyze"
    priority: int = 0


class SimulateLoadRequest(BaseModel):
    count: int = 5  # number of work items to enqueue at once


# ---------------------------------------------------------------------------
# Cluster status
# ---------------------------------------------------------------------------

@router.get("/status")
async def cluster_status():
    """Full cluster status: replicas, queue, config, scale events."""
    coord = get_coordinator()
    return coord.get_status()


# ---------------------------------------------------------------------------
# MAPE-K tick (manually trigger one control loop iteration)
# ---------------------------------------------------------------------------

@router.post("/tick")
async def mape_k_tick():
    """
    Run one MAPE-K control loop iteration.
    The coordinator will monitor, analyze, plan, and execute.
    Returns the decision made (spawn, kill, or none).
    Also runs pending network validation after scale events.
    """
    coord = get_coordinator()
    result = coord.mape_k_tick()

    # Run pending validation if a scale event occurred
    validation = await coord.run_pending_validation()
    if validation:
        result["validation"] = validation

    return result


# ---------------------------------------------------------------------------
# Enqueue work
# ---------------------------------------------------------------------------

@router.post("/enqueue")
async def enqueue_work(body: EnqueueRequest):
    """Add a work item to the agent queue."""
    coord = get_coordinator()
    item = coord.enqueue(body.service_name, body.task_type, body.priority)
    return {"status": "enqueued", "work_id": item.id, "queue_depth": sum(1 for i in coord.work_queue if i.status == "pending")}


# ---------------------------------------------------------------------------
# Simulate load (for demo — flood the queue to trigger auto-scaling)
# ---------------------------------------------------------------------------

@router.post("/simulate-load")
async def simulate_load(body: SimulateLoadRequest):
    """
    Enqueue multiple work items at once to simulate a traffic spike.
    This will trigger the MAPE-K loop to spawn new agent replicas.
    Bypasses cooldown so the demo shows rapid scaling.
    """
    coord = get_coordinator()
    services = coord._all_services or ["payment-service", "order-service", "auth-service", "api-gateway", "inventory-svc"]
    items = []
    for i in range(body.count):
        svc = services[i % len(services)]
        item = coord.enqueue(svc, "analyze", priority=i)
        items.append(item.id)

    # Run multiple MAPE-K ticks with cooldown bypass so demo shows scaling
    results = []
    for _ in range(min(body.count, 4)):
        coord._last_scale_time = 0  # bypass cooldown for demo
        result = coord.mape_k_tick()
        results.append(result)
        if result["action"] == "none":
            break

    # Run network validation after scaling completes
    validation = await coord.run_pending_validation()

    return {
        "status": "load_simulated",
        "items_enqueued": len(items),
        "scale_actions": [r["action"] for r in results if r["action"] != "none"],
        "final_replicas": len(coord.replicas),
        "mape_k_result": results[-1] if results else {},
        "validation": validation,
    }


# ---------------------------------------------------------------------------
# Network validation endpoints
# ---------------------------------------------------------------------------

@router.post("/validate")
async def trigger_validation():
    """Manually trigger network validation against all Forge API endpoints."""
    from cluster.validation import validate_network_after_scale

    result = await validate_network_after_scale(
        trigger_event="manual",
        trigger_replica="api-manual",
    )
    result_dict = result.to_dict()

    coord = get_coordinator()
    coord.validation_results.append(result_dict)
    if len(coord.validation_results) > 20:
        coord.validation_results = coord.validation_results[-20:]

    return result_dict


@router.get("/validations")
async def list_validations():
    """List recent network validation results."""
    coord = get_coordinator()
    return {
        "validations": coord.validation_results[-10:],
        "count": len(coord.validation_results),
    }


# ---------------------------------------------------------------------------
# Complete work (mark item done — for demo simulation)
# ---------------------------------------------------------------------------

@router.post("/complete/{work_id}")
async def complete_work(work_id: str):
    coord = get_coordinator()
    coord.complete_work(work_id, success=True)
    return {"status": "completed", "work_id": work_id}


# ---------------------------------------------------------------------------
# Scale events history
# ---------------------------------------------------------------------------

@router.get("/events")
async def scale_events():
    coord = get_coordinator()
    return {"events": coord.scale_events, "count": len(coord.scale_events)}
