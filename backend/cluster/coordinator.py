"""
Forge Cluster Coordinator — MAPE-K self-replicating agent manager.

Implements the Monitor-Analyze-Plan-Execute-Knowledge loop:
  Monitor  — track work queue depth, agent load, services per agent
  Analyze  — detect when agents are overloaded
  Plan     — decide to spawn/kill agent replicas
  Execute  — create new agent workers, rebalance partitions
  Knowledge — persist cluster state to memory

For the hackathon demo this runs in-process with simulated agents.
In production this would use K8s HPA + Datadog WatermarkPodAutoscaler.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Configuration (tuned for demo — low thresholds so replication triggers fast)
# ---------------------------------------------------------------------------

MAX_SERVICES_PER_AGENT = 5          # spawn new agent when exceeded
QUEUE_HIGH_WATERMARK = 3            # spawn when queue depth > this
QUEUE_LOW_WATERMARK = 1             # kill replica when queue depth < this
SCALE_COOLDOWN_SECONDS = 15         # minimum time between scale events
MAX_AGENT_REPLICAS = 6              # cap for demo
MIN_AGENT_REPLICAS = 1              # always keep at least one


# ---------------------------------------------------------------------------
# Agent Replica — represents one running agent worker
# ---------------------------------------------------------------------------

@dataclass
class AgentReplica:
    replica_id: str
    name: str
    status: str = "running"                 # running | draining | stopped
    assigned_services: list[str] = field(default_factory=list)
    analyses_completed: int = 0
    current_task: str | None = None
    spawned_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_heartbeat: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cpu_load: float = 0.0                   # simulated 0-100
    memory_mb: float = 0.0                  # simulated

    def to_dict(self) -> dict:
        return {
            "replica_id": self.replica_id,
            "name": self.name,
            "status": self.status,
            "assigned_services": self.assigned_services,
            "analyses_completed": self.analyses_completed,
            "current_task": self.current_task,
            "spawned_at": self.spawned_at,
            "last_heartbeat": self.last_heartbeat,
            "cpu_load": round(self.cpu_load, 1),
            "memory_mb": round(self.memory_mb, 1),
        }


# ---------------------------------------------------------------------------
# Work queue item
# ---------------------------------------------------------------------------

@dataclass
class WorkItem:
    id: str
    service_name: str
    task_type: str          # "analyze" | "generate_insights"
    priority: int = 0       # higher = more urgent
    enqueued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    assigned_to: str | None = None
    status: str = "pending"  # pending | processing | completed | failed


# ---------------------------------------------------------------------------
# Cluster Coordinator (singleton)
# ---------------------------------------------------------------------------

class ClusterCoordinator:
    """
    In-process cluster coordinator for demo purposes.
    Manages agent replicas, work queue, partitioning, and auto-scaling.
    """

    _instance: "ClusterCoordinator | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "ClusterCoordinator":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        self.replicas: dict[str, AgentReplica] = {}
        self.work_queue: deque[WorkItem] = deque()
        self.completed_work: list[WorkItem] = []
        self.scale_events: list[dict] = []
        self._last_scale_time: float = 0
        self._mape_running = False
        self._all_services: list[str] = []
        self.validation_results: list[dict] = []
        self._pending_validation: tuple[str, str] | None = None

        # Spawn the initial (primary) agent
        self._spawn_replica("forge-primary")

    # ------------------------------------------------------------------
    # Replica management
    # ------------------------------------------------------------------

    def _spawn_replica(self, name: str | None = None) -> AgentReplica:
        rid = f"agent-{uuid.uuid4().hex[:6]}"
        name = name or f"forge-{rid[-6:]}"
        replica = AgentReplica(replica_id=rid, name=name)
        self.replicas[rid] = replica

        self.scale_events.append({
            "event": "spawn",
            "replica_id": rid,
            "name": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": "auto-scale" if len(self.replicas) > 1 else "initial",
            "total_replicas": len(self.replicas),
        })
        self._last_scale_time = time.monotonic()
        if len(self.replicas) > 1:
            self._pending_validation = ("scale_up", rid)
        self._rebalance_partitions()
        return replica

    def _kill_replica(self, rid: str) -> None:
        if rid not in self.replicas or len(self.replicas) <= MIN_AGENT_REPLICAS:
            return
        replica = self.replicas[rid]
        replica.status = "draining"

        # Reassign any in-flight work
        for item in self.work_queue:
            if item.assigned_to == rid:
                item.assigned_to = None
                item.status = "pending"

        self.scale_events.append({
            "event": "kill",
            "replica_id": rid,
            "name": replica.name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": "scale-down",
            "total_replicas": len(self.replicas) - 1,
        })

        del self.replicas[rid]
        self._last_scale_time = time.monotonic()
        self._pending_validation = ("scale_down", rid)
        self._rebalance_partitions()

    def _rebalance_partitions(self) -> None:
        """Distribute services evenly across running replicas."""
        running = [r for r in self.replicas.values() if r.status == "running"]
        if not running or not self._all_services:
            return

        # Clear current assignments
        for r in running:
            r.assigned_services = []

        # Round-robin assign
        for i, svc in enumerate(self._all_services):
            running[i % len(running)].assigned_services.append(svc)

    def set_services(self, services: list[str]) -> None:
        """Update the list of known services (called from API/graph)."""
        self._all_services = list(services)
        self._rebalance_partitions()

    # ------------------------------------------------------------------
    # Work queue
    # ------------------------------------------------------------------

    def enqueue(self, service_name: str, task_type: str = "analyze", priority: int = 0) -> WorkItem:
        item = WorkItem(
            id=f"work-{uuid.uuid4().hex[:6]}",
            service_name=service_name,
            task_type=task_type,
            priority=priority,
        )
        self.work_queue.append(item)
        return item

    def dequeue(self, replica_id: str) -> WorkItem | None:
        """Pull the next pending item and assign it to a replica."""
        for item in self.work_queue:
            if item.status == "pending":
                item.status = "processing"
                item.assigned_to = replica_id
                if replica_id in self.replicas:
                    self.replicas[replica_id].current_task = f"{item.task_type}:{item.service_name}"
                return item
        return None

    def complete_work(self, work_id: str, success: bool = True) -> None:
        for item in list(self.work_queue):
            if item.id == work_id:
                item.status = "completed" if success else "failed"
                self.work_queue.remove(item)
                self.completed_work.append(item)
                if item.assigned_to and item.assigned_to in self.replicas:
                    r = self.replicas[item.assigned_to]
                    r.analyses_completed += 1
                    r.current_task = None
                # Keep only last 50 completed
                if len(self.completed_work) > 50:
                    self.completed_work = self.completed_work[-50:]
                return

    # ------------------------------------------------------------------
    # MAPE-K loop
    # ------------------------------------------------------------------

    def mape_k_tick(self) -> dict:
        """
        Run one iteration of the MAPE-K control loop.
        Returns the decision made (for API/UI visibility).
        """
        import random

        # --- MONITOR ---
        queue_depth = sum(1 for i in self.work_queue if i.status == "pending")
        running = [r for r in self.replicas.values() if r.status == "running"]
        replica_count = len(running)
        services_per_agent = len(self._all_services) / max(replica_count, 1)

        # Simulate CPU/memory for demo
        for r in running:
            load_factor = len(r.assigned_services) / max(MAX_SERVICES_PER_AGENT, 1)
            task_factor = 1.5 if r.current_task else 0.3
            r.cpu_load = min(99, max(5, load_factor * 40 + task_factor * 30 + random.uniform(-5, 10)))
            r.memory_mb = min(2048, max(64, load_factor * 300 + task_factor * 200 + random.uniform(-20, 50)))
            r.last_heartbeat = datetime.now(timezone.utc).isoformat()

        metrics = {
            "queue_depth": queue_depth,
            "replica_count": replica_count,
            "services_per_agent": round(services_per_agent, 1),
            "avg_cpu": round(sum(r.cpu_load for r in running) / max(len(running), 1), 1),
            "avg_memory_mb": round(sum(r.memory_mb for r in running) / max(len(running), 1), 1),
        }

        # --- ANALYZE ---
        should_scale_up = False
        should_scale_down = False
        reason = ""

        cooldown_ok = (time.monotonic() - self._last_scale_time) > SCALE_COOLDOWN_SECONDS

        if queue_depth > QUEUE_HIGH_WATERMARK and replica_count < MAX_AGENT_REPLICAS and cooldown_ok:
            should_scale_up = True
            reason = f"queue_depth={queue_depth} > high_watermark={QUEUE_HIGH_WATERMARK}"
        elif services_per_agent > MAX_SERVICES_PER_AGENT and replica_count < MAX_AGENT_REPLICAS and cooldown_ok:
            should_scale_up = True
            reason = f"services_per_agent={services_per_agent:.0f} > max={MAX_SERVICES_PER_AGENT}"
        elif metrics["avg_cpu"] > 80 and replica_count < MAX_AGENT_REPLICAS and cooldown_ok:
            should_scale_up = True
            reason = f"avg_cpu={metrics['avg_cpu']}% > 80%"
        elif queue_depth < QUEUE_LOW_WATERMARK and replica_count > MIN_AGENT_REPLICAS and cooldown_ok:
            should_scale_down = True
            reason = f"queue_depth={queue_depth} < low_watermark={QUEUE_LOW_WATERMARK}"

        # --- PLAN + EXECUTE ---
        action = "none"
        if should_scale_up:
            new_replica = self._spawn_replica()
            action = f"spawned {new_replica.name} ({reason})"
        elif should_scale_down:
            # Kill the replica with the least work
            least_busy = min(running, key=lambda r: len(r.assigned_services))
            if least_busy.name != "forge-primary":
                self._kill_replica(least_busy.replica_id)
                action = f"killed {least_busy.name} ({reason})"

        # --- Process one work item per running replica ---
        for r in running:
            if r.current_task is None:
                self.dequeue(r.replica_id)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "action": action,
            "replicas": [r.to_dict() for r in self.replicas.values()],
        }

    # ------------------------------------------------------------------
    # Network validation after scale events
    # ------------------------------------------------------------------

    async def run_pending_validation(self) -> dict | None:
        """Run network validation if a scale event is pending."""
        if self._pending_validation is None:
            return None

        trigger_event, trigger_replica = self._pending_validation
        self._pending_validation = None

        from cluster.validation import validate_network_after_scale
        result = await validate_network_after_scale(trigger_event, trigger_replica)
        result_dict = result.to_dict()
        self.validation_results.append(result_dict)
        # Keep only last 20
        if len(self.validation_results) > 20:
            self.validation_results = self.validation_results[-20:]
        return result_dict

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        running = [r for r in self.replicas.values() if r.status == "running"]
        pending = sum(1 for i in self.work_queue if i.status == "pending")
        return {
            "cluster_id": "forge-cluster-demo",
            "total_replicas": len(self.replicas),
            "running_replicas": len(running),
            "pending_work_items": pending,
            "processing_work_items": sum(1 for i in self.work_queue if i.status == "processing"),
            "completed_analyses": sum(r.analyses_completed for r in self.replicas.values()),
            "total_services": len(self._all_services),
            "services_per_agent": round(len(self._all_services) / max(len(running), 1), 1),
            "replicas": [r.to_dict() for r in self.replicas.values()],
            "recent_scale_events": self.scale_events[-10:],
            "validation_results": self.validation_results[-5:],
            "last_validation": self.validation_results[-1] if self.validation_results else None,
            "config": {
                "max_services_per_agent": MAX_SERVICES_PER_AGENT,
                "queue_high_watermark": QUEUE_HIGH_WATERMARK,
                "queue_low_watermark": QUEUE_LOW_WATERMARK,
                "max_replicas": MAX_AGENT_REPLICAS,
                "min_replicas": MIN_AGENT_REPLICAS,
                "scale_cooldown_seconds": SCALE_COOLDOWN_SECONDS,
            },
        }


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

def get_coordinator() -> ClusterCoordinator:
    return ClusterCoordinator()
