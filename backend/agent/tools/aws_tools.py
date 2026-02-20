"""
AWS remediation action tools for the Strands agent.
boto3 calls are mocked in DEMO_MODE — same interface, fake responses.
In production, remove the mock layer and ensure IAM roles are configured.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

from strands import tool

# In-memory action log (replace with Redis/DB in production)
_action_log: list[dict] = []

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"


def _record_action(action_type: str, service: str, params: dict, result: dict) -> dict:
    entry = {
        "id": str(uuid.uuid4())[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action_type": action_type,
        "service": service,
        "parameters": params,
        "status": result.get("status", "success"),
        "result": result,
        "triggered_by": "agent",
    }
    _action_log.append(entry)
    return entry


def get_action_log() -> list[dict]:
    return list(reversed(_action_log))  # most recent first


@tool
async def scale_ecs_service(
    cluster: str,
    service: str,
    desired_count: int,
    reason: str,
) -> str:
    """
    Scale an ECS Fargate service to a new desired task count.
    Use this when a service is under load or experiencing latency due to insufficient capacity.

    Args:
        cluster: ECS cluster name (e.g., "forge-prod-cluster")
        service: ECS service name (e.g., "payment-service")
        desired_count: Target number of running tasks (e.g., 6)
        reason: Why we are scaling (for audit trail)

    Returns:
        JSON confirming the scaling action with previous and new task count
    """
    params = {"cluster": cluster, "service": service, "desired_count": desired_count, "reason": reason}

    if DEMO_MODE:
        previous_count = 2
        result = {
            "status": "success",
            "message": f"[DEMO] Scaled {service} from {previous_count} → {desired_count} tasks",
            "cluster": cluster,
            "service": service,
            "previous_desired_count": previous_count,
            "new_desired_count": desired_count,
            "stabilization_estimate_seconds": 45,
        }
    else:
        import boto3
        client = boto3.client("ecs", region_name=os.getenv("AWS_REGION", "us-east-1"))
        resp = client.update_service(
            cluster=cluster,
            service=service,
            desiredCount=desired_count,
        )
        svc = resp["service"]
        result = {
            "status": "success",
            "service": service,
            "previous_desired_count": svc.get("desiredCount"),
            "new_desired_count": desired_count,
            "running_count": svc.get("runningCount"),
        }

    entry = _record_action("scale_ecs", service, params, result)
    return json.dumps({**result, "action_id": entry["id"]}, default=str)


@tool
async def trigger_codedeploy_rollback(
    application_name: str,
    deployment_group: str,
    reason: str,
) -> str:
    """
    Trigger a CodeDeploy rollback for a deployment group, reverting to the
    last known-good revision. Use after identifying that a recent deployment
    is the root cause of a service degradation.

    Args:
        application_name: CodeDeploy application name (e.g., "payment-service-app")
        deployment_group: Deployment group name (e.g., "payment-service-prod")
        reason: Reason for rollback (for audit trail)

    Returns:
        JSON with the rollback deployment ID and status
    """
    params = {
        "application_name": application_name,
        "deployment_group": deployment_group,
        "reason": reason,
    }

    if DEMO_MODE:
        rollback_id = f"d-{uuid.uuid4().hex[:8].upper()}"
        result = {
            "status": "success",
            "message": f"[DEMO] Rollback initiated for {deployment_group}",
            "rollback_deployment_id": rollback_id,
            "application_name": application_name,
            "deployment_group": deployment_group,
            "reverts_to": "v2.3.0",  # last stable
            "estimated_completion_seconds": 90,
        }
    else:
        import boto3
        client = boto3.client("codedeploy", region_name=os.getenv("AWS_REGION", "us-east-1"))
        resp = client.create_deployment(
            applicationName=application_name,
            deploymentGroupName=deployment_group,
            autoRollbackConfiguration={"enabled": True},
            description=f"Emergency rollback: {reason}",
        )
        result = {
            "status": "success",
            "rollback_deployment_id": resp["deploymentId"],
        }

    entry = _record_action("rollback_deploy", deployment_group, params, result)
    return json.dumps({**result, "action_id": entry["id"]}, default=str)


@tool
async def update_ssm_parameter(
    parameter_name: str,
    value: str,
    description: str,
    service: str,
) -> str:
    """
    Update an AWS SSM Parameter Store value for a service configuration.
    Use to adjust timeouts, circuit-breaker thresholds, feature flags, or rate limits
    at runtime without a deployment.

    Args:
        parameter_name: Full SSM parameter path (e.g., "/forge/payment-service/timeout_ms")
        value: New parameter value (e.g., "5000")
        description: Why this parameter is being changed
        service: Which service this parameter belongs to

    Returns:
        JSON confirming the parameter update with old and new values
    """
    params = {
        "parameter_name": parameter_name,
        "value": value,
        "description": description,
        "service": service,
    }

    if DEMO_MODE:
        result = {
            "status": "success",
            "message": f"[DEMO] Updated SSM parameter {parameter_name}",
            "parameter_name": parameter_name,
            "old_value": "30000",
            "new_value": value,
            "version": 2,
        }
    else:
        import boto3
        client = boto3.client("ssm", region_name=os.getenv("AWS_REGION", "us-east-1"))
        resp = client.put_parameter(
            Name=parameter_name,
            Value=value,
            Type="String",
            Overwrite=True,
            Description=description,
        )
        result = {
            "status": "success",
            "parameter_name": parameter_name,
            "version": resp["Version"],
            "new_value": value,
        }

    entry = _record_action("update_ssm_param", service, params, result)
    return json.dumps({**result, "action_id": entry["id"]}, default=str)
