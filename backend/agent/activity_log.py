"""
Agent activity log — captures tool calls and analysis steps in real-time.

This is an in-memory ring buffer that the frontend can poll to show
live agent activity (tool calls, insights stored, analysis steps).
"""
from __future__ import annotations

import time
import threading
from collections import deque
from typing import Any

_MAX_ENTRIES = 200
_lock = threading.Lock()
_log: deque[dict[str, Any]] = deque(maxlen=_MAX_ENTRIES)
_counter = 0


def log_activity(
    event_type: str,
    summary: str,
    detail: str = "",
    source: str = "claude",
    metadata: dict | None = None,
) -> int:
    """
    Append an activity entry.

    event_type: tool_call | insight_stored | analysis | error | minimax
    source: claude | minimax | system
    """
    global _counter
    with _lock:
        _counter += 1
        entry = {
            "id": _counter,
            "ts": time.time(),
            "event_type": event_type,
            "source": source,
            "summary": summary,
            "detail": detail[:500],  # cap detail length
            "metadata": metadata or {},
        }
        _log.append(entry)
        return _counter


def get_recent_activity(since_id: int = 0, limit: int = 50) -> list[dict]:
    """Return activity entries with id > since_id, newest first."""
    with _lock:
        items = [e for e in _log if e["id"] > since_id]
    return sorted(items, key=lambda x: x["id"], reverse=True)[:limit]


def get_all_activity(limit: int = 100) -> list[dict]:
    """Return the most recent activity entries, newest first."""
    with _lock:
        items = list(_log)
    return sorted(items, key=lambda x: x["id"], reverse=True)[:limit]
