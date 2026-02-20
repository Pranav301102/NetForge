"""Pydantic models for agent structured output."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class AnomalyType(str, Enum):
    LATENCY_SPIKE = "latency_spike"
    ERROR_RATE = "error_rate"
    THROUGHPUT_DROP = "throughput_drop"
    CASCADING = "cascading_failure"
    EXTERNAL_DEPENDENCY = "external_dependency"


class Anomaly(BaseModel):
    type: AnomalyType
    service: str
    metric: str
    current_value: float
    baseline_value: float
    severity: str  # "low" | "medium" | "high" | "critical"
    description: str


class RecentChange(BaseModel):
    service: str
    change_type: str  # "deployment" | "config" | "scale"
    version: str | None = None
    deployed_at: datetime
    status: str
    correlation: str  # how this might relate to the anomaly


class ServiceHealth(BaseModel):
    service: str
    health_score: float = Field(ge=0, le=100)
    status: HealthStatus
    avg_latency_ms: float
    p99_latency_ms: float
    error_rate_pct: float
    anomalies: list[Anomaly] = []
    recent_changes: list[RecentChange] = []
    upstream_affected: list[str] = []
    recommended_action: str
    root_cause: str | None = None
    confidence: float = Field(ge=0, le=1, default=0.0)


class RemediationAction(BaseModel):
    id: str
    timestamp: datetime
    action_type: str  # "scale_ecs" | "rollback_deploy" | "update_param"
    service: str
    parameters: dict[str, Any]
    status: str  # "pending" | "executing" | "success" | "failed"
    result: str | None = None
    triggered_by: str  # "agent" | "user"


class ValidationResult(BaseModel):
    service: str
    test_suite: str
    passed: int
    failed: int
    pass_rate: float
    latency_p99_ms: float
    baseline_p99_ms: float
    recovered: bool
    details: str


class AgentAnalysisReport(BaseModel):
    """Top-level report returned by the agent for a given analysis run."""
    run_id: str
    timestamp: datetime
    services_analyzed: list[str]
    root_cause_service: str | None
    root_cause_summary: str
    affected_services: list[ServiceHealth]
    actions_taken: list[RemediationAction] = []
    validation: ValidationResult | None = None
    chat_summary: str  # Human-readable summary for the CopilotKit chat
