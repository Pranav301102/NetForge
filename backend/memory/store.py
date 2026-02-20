"""
Thread-safe JSON file-based memory store for Forge insights engine.

Persists service patterns, insights, baselines, and analysis history
to a local JSON file so the agent accumulates knowledge over time.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MEMORY_DIR = Path(__file__).parent
_MEMORY_FILE = _MEMORY_DIR / "insights.json"
_lock = threading.Lock()


def _default_memory() -> dict:
    return {
        "version": "1.0",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "services": {},
        "global_patterns": [],
        "analysis_history": [],
    }


def _ensure_service(data: dict, service: str) -> None:
    if service not in data["services"]:
        data["services"][service] = {
            "baseline_metrics": {},
            "patterns": [],
            "insights": [],
        }


# ---------------------------------------------------------------------------
# Core I/O
# ---------------------------------------------------------------------------

def load_memory() -> dict:
    """Read the JSON memory file, creating a default one if missing."""
    with _lock:
        if not _MEMORY_FILE.exists():
            data = _default_memory()
            _atomic_write(data)
            return data
        with open(_MEMORY_FILE, "r") as f:
            return json.load(f)


def save_memory(data: dict) -> None:
    """Atomically write the memory dict to disk."""
    with _lock:
        _atomic_write(data)


def _atomic_write(data: dict) -> None:
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp = _MEMORY_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    tmp.replace(_MEMORY_FILE)


# ---------------------------------------------------------------------------
# Insight helpers
# ---------------------------------------------------------------------------

def add_insight(service: str, insight: dict) -> str:
    """Append an insight to a service's list. Returns the generated ID."""
    data = load_memory()
    _ensure_service(data, service)
    insight_id = insight.get("id") or f"ins-{uuid.uuid4().hex[:8]}"
    insight["id"] = insight_id
    insight.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    insight.setdefault("status", "open")
    data["services"][service]["insights"].append(insight)
    save_memory(data)
    return insight_id


def get_all_insights(status: str | None = None) -> list[dict]:
    """Return all insights across services, optionally filtered by status."""
    data = load_memory()
    results: list[dict] = []
    for svc_name, svc_data in data.get("services", {}).items():
        for ins in svc_data.get("insights", []):
            entry = {**ins, "service": svc_name}
            if status is None or entry.get("status") == status:
                results.append(entry)
    results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return results


def update_insight_status(insight_id: str, status: str) -> bool:
    """Mark an insight as acknowledged/resolved. Returns True if found."""
    data = load_memory()
    for svc_data in data.get("services", {}).values():
        for ins in svc_data.get("insights", []):
            if ins.get("id") == insight_id:
                ins["status"] = status
                save_memory(data)
                return True
    return False


# ---------------------------------------------------------------------------
# Pattern helpers
# ---------------------------------------------------------------------------

def add_pattern(service: str, pattern: dict) -> str:
    """Append or update a pattern (merge by similar description)."""
    data = load_memory()
    _ensure_service(data, service)
    patterns = data["services"][service]["patterns"]

    # Try to merge with existing pattern of same type
    for existing in patterns:
        if existing.get("type") == pattern.get("type") and _similar(existing.get("description", ""), pattern.get("description", "")):
            existing["last_confirmed"] = datetime.now(timezone.utc).isoformat()
            existing["occurrences"] = existing.get("occurrences", 1) + 1
            existing["confidence"] = min(0.99, existing.get("confidence", 0.5) + 0.02)
            if pattern.get("recommendation"):
                existing["recommendation"] = pattern["recommendation"]
            save_memory(data)
            return existing["id"]

    pat_id = pattern.get("id") or f"pat-{uuid.uuid4().hex[:8]}"
    pattern["id"] = pat_id
    pattern.setdefault("first_detected", datetime.now(timezone.utc).isoformat())
    pattern.setdefault("last_confirmed", datetime.now(timezone.utc).isoformat())
    pattern.setdefault("occurrences", 1)
    patterns.append(pattern)
    save_memory(data)
    return pat_id


def add_global_pattern(pattern: dict) -> str:
    """Append a global (cross-service) pattern."""
    data = load_memory()
    pat_id = pattern.get("id") or f"gpat-{uuid.uuid4().hex[:8]}"
    pattern["id"] = pat_id
    data["global_patterns"].append(pattern)
    save_memory(data)
    return pat_id


# ---------------------------------------------------------------------------
# Service memory
# ---------------------------------------------------------------------------

def get_service_memory(service: str) -> dict:
    """Get all stored memory for a single service."""
    data = load_memory()
    _ensure_service(data, service)
    return {
        "service": service,
        **data["services"][service],
    }


def update_baseline(service: str, metrics: dict) -> None:
    """Update the baseline metrics for a service."""
    data = load_memory()
    _ensure_service(data, service)
    data["services"][service]["baseline_metrics"] = {
        **metrics,
        "measured_at": datetime.now(timezone.utc).isoformat(),
    }
    save_memory(data)


# ---------------------------------------------------------------------------
# Analysis history
# ---------------------------------------------------------------------------

def record_analysis(session: dict) -> str:
    """Append to analysis_history and return session_id."""
    data = load_memory()
    session_id = session.get("session_id") or f"sess-{uuid.uuid4().hex[:8]}"
    session["session_id"] = session_id
    session.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    data["analysis_history"].append(session)
    # Keep last 100 entries
    if len(data["analysis_history"]) > 100:
        data["analysis_history"] = data["analysis_history"][-100:]
    save_memory(data)
    return session_id


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def get_all_patterns() -> list[dict]:
    """Return all patterns (service-level + global)."""
    data = load_memory()
    results: list[dict] = []
    for svc_name, svc_data in data.get("services", {}).items():
        for pat in svc_data.get("patterns", []):
            results.append({**pat, "service": svc_name})
    for gpat in data.get("global_patterns", []):
        results.append({**gpat, "scope": "global"})
    return results


def get_recommendations() -> list[dict]:
    """Return all open high/critical severity insights that have recommendations."""
    insights = get_all_insights(status="open")
    return [
        ins for ins in insights
        if ins.get("severity") in ("high", "critical") and ins.get("recommendation")
    ]


def _similar(a: str, b: str) -> bool:
    """Simple similarity check — same first 40 chars or >60% word overlap."""
    if a[:40].lower() == b[:40].lower():
        return True
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b) / max(len(words_a), len(words_b))
    return overlap > 0.6
