"""
Strands agent tools for persistent memory operations.

These @tool-decorated functions let the agent read/write the JSON-based
knowledge store so it can learn patterns over time and provide
context-aware recommendations.
"""
from __future__ import annotations

from strands import tool

from memory.store import (
    add_insight,
    add_pattern,
    get_all_insights,
    get_all_patterns,
    get_recommendations,
    get_service_memory,
)


@tool
def store_insight(
    service_name: str,
    category: str,
    severity: str,
    title: str,
    insight: str,
    evidence: str,
    recommendation: str,
) -> str:
    """
    Persist a new insight about a service to the memory store.

    Args:
        service_name: The service this insight is about (e.g. "payment-service").
        category: One of: optimization, cost, reliability, performance.
        severity: One of: low, medium, high, critical.
        title: Short descriptive title for the insight.
        insight: Detailed description of the finding.
        evidence: JSON string of supporting evidence / metrics.
        recommendation: What action should be taken.

    Returns:
        The generated insight ID.
    """
    insight_id = add_insight(service_name, {
        "category": category,
        "severity": severity,
        "title": title,
        "insight": insight,
        "evidence": evidence,
        "recommendation": recommendation,
    })
    # Log to activity feed
    try:
        from agent.activity_log import log_activity
        log_activity(
            "insight_stored",
            f"[{severity.upper()}] {title}",
            detail=f"{service_name}: {insight[:200]}",
            source="minimax",
            metadata={"service": service_name, "category": category, "severity": severity, "insight_id": insight_id},
        )
    except Exception:
        pass
    return f"Insight stored: {insight_id}"


@tool
def store_pattern(
    service_name: str,
    pattern_type: str,
    description: str,
    confidence: float,
    recommendation: str,
) -> str:
    """
    Persist a detected pattern for a service. If a similar pattern already
    exists it will be merged (occurrence count incremented, confidence updated).

    Args:
        service_name: The service this pattern is about.
        pattern_type: One of: periodic_overload, latency_spike, cascade_risk, dependency_bottleneck.
        description: Human-readable description of the pattern.
        confidence: Confidence score between 0.0 and 1.0.
        recommendation: Suggested mitigation or pre-emptive action.

    Returns:
        The pattern ID (new or existing).
    """
    pat_id = add_pattern(service_name, {
        "type": pattern_type,
        "description": description,
        "confidence": confidence,
        "recommendation": recommendation,
    })
    # Log to activity feed
    try:
        from agent.activity_log import log_activity
        log_activity(
            "pattern_stored",
            f"Pattern: {pattern_type} on {service_name}",
            detail=description[:200],
            source="minimax",
            metadata={"service": service_name, "pattern_type": pattern_type, "confidence": confidence},
        )
    except Exception:
        pass
    return f"Pattern stored: {pat_id}"


@tool
def recall_service_history(service_name: str) -> str:
    """
    Retrieve all past insights, patterns, and baseline metrics for a service
    from the persistent memory store.

    Args:
        service_name: The service to look up.

    Returns:
        JSON string with the service's full memory (baseline_metrics, patterns, insights).
    """
    import json
    mem = get_service_memory(service_name)
    return json.dumps(mem, indent=2, default=str)


@tool
def recall_similar_incidents() -> str:
    """
    Search memory for all detected patterns across every service.
    Useful for finding cross-service correlations and recurring issues.

    Returns:
        JSON string with all stored patterns (service-level and global).
    """
    import json
    patterns = get_all_patterns()
    if not patterns:
        return "No patterns stored yet."
    return json.dumps(patterns, indent=2, default=str)


@tool
def get_optimization_recommendations() -> str:
    """
    Return all open high-severity and critical insights that include
    actionable recommendations. These are the top-priority items the
    platform operator should address.

    Returns:
        JSON string with the list of recommendations.
    """
    import json
    recs = get_recommendations()
    if not recs:
        return "No open high-severity recommendations at this time."
    return json.dumps(recs, indent=2, default=str)
