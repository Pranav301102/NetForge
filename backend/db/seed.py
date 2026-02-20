"""
Seed Neo4j with a realistic fintech microservice dependency graph + deployment history.

Run:  python -m db.seed
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db.neo4j_client import get_driver, close_driver

_now = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# 24-service topology (fintech / e-commerce platform)
# name, type, team, criticality, base_health, base_avg_ms, base_p99_ms
# ---------------------------------------------------------------------------
SERVICES = [
    # ─── Edge / gateway ───────────────────────────────────────────────────
    ("api-gateway",          "gateway",   "platform",  "critical", 98,  8,   28),
    ("auth-service",         "internal",  "platform",  "critical", 97,  6,   18),
    # ─── Core domain services ─────────────────────────────────────────────
    ("user-service",         "internal",  "users",     "high",     98,  9,   30),
    ("order-service",        "internal",  "orders",    "critical", 97,  14,  45),
    ("checkout-service",     "internal",  "orders",    "critical", 96,  18,  60),
    ("payment-service",      "internal",  "payments",  "critical", 42,  420, 1800),  # ← DEGRADED
    ("inventory-service",    "internal",  "catalog",   "high",     98,  11,  38),
    ("catalog-service",      "internal",  "catalog",   "medium",   99,  10,  32),
    ("fraud-detection-svc",  "internal",  "payments",  "high",     97,  35,  110),
    ("shipping-service",     "internal",  "logistics", "high",     98,  22,  70),
    ("wallet-service",       "internal",  "payments",  "high",     95,  28,  95),
    ("kyc-service",          "internal",  "compliance","medium",   99,  40,  130),
    ("coupon-service",       "internal",  "marketing", "medium",   99,  7,   22),
    ("review-service",       "internal",  "catalog",   "low",      99,  12,  40),
    ("notification-svc",     "internal",  "platform",  "medium",   98,  6,   20),
    ("search-service",       "internal",  "catalog",   "medium",   98,  22,  85),
    ("recommendation-svc",   "internal",  "data",      "low",      99,  35,  110),
    ("analytics-service",    "internal",  "data",      "low",      99,  25,  90),
    # ─── Infrastructure ───────────────────────────────────────────────────
    ("postgres-orders",      "database",  "platform",  "critical", 99,  3,   12),
    ("postgres-catalog",     "database",  "platform",  "high",     99,  3,   11),
    ("redis-cache",          "cache",     "platform",  "high",     99,  1,   4),
    # ─── External dependencies ─────────────────────────────────────────────
    ("payment-gateway",      "external",  "external",  "critical", 61,  340, 1200),  # ← ROOT CAUSE
    ("twilio-sms",           "external",  "external",  "low",      99,  80,  200),
    ("sendgrid-email",       "external",  "external",  "low",      99,  60,  180),
]

# (caller, callee, avg_ms, p99_ms, rpm)
CALL_EDGES = [
    # API gateway fans out
    ("api-gateway",         "auth-service",        5,   15,  2000),
    ("api-gateway",         "user-service",        9,   30,   800),
    ("api-gateway",         "order-service",       14,  48,   600),
    ("api-gateway",         "search-service",      22,  88,  1200),
    ("api-gateway",         "catalog-service",     10,  33,   900),
    ("api-gateway",         "recommendation-svc",  38, 120,   400),
    # Order flow
    ("order-service",       "checkout-service",    18,  60,   400),
    ("order-service",       "inventory-service",   11,  38,   400),
    ("order-service",       "shipping-service",    24,  75,   200),
    ("order-service",       "notification-svc",     6,  22,   400),
    ("order-service",       "postgres-orders",      3,  12,  1600),
    ("order-service",       "coupon-service",       7,  22,   200),
    # Checkout → payment (HOT PATH — affected by payment degradation)
    ("checkout-service",    "payment-service",     420, 1800,  200),  # ← HIGH LATENCY
    ("checkout-service",    "fraud-detection-svc", 36, 115,   200),
    ("checkout-service",    "inventory-service",   11,  38,   200),
    ("checkout-service",    "wallet-service",      30, 100,   100),
    ("checkout-service",    "redis-cache",          1,   4,   800),
    # Payment → external (ROOT CAUSE EDGE)
    ("payment-service",     "payment-gateway",    340, 1200,  200),  # ← EXTERNAL BOTTLENECK
    ("payment-service",     "fraud-detection-svc", 36, 110,   200),
    ("payment-service",     "postgres-orders",      3,  12,   600),
    # Wallet
    ("wallet-service",      "payment-service",    420, 1800,  100),  # cascading
    ("wallet-service",      "kyc-service",         42, 135,   100),
    ("wallet-service",      "postgres-orders",      3,  12,   300),
    # Users
    ("user-service",        "postgres-orders",      3,  10,   400),
    ("user-service",        "redis-cache",          1,   3,  1200),
    ("user-service",        "kyc-service",         42, 135,   100),
    # Catalog / inventory
    ("catalog-service",     "postgres-catalog",     3,  11,   600),
    ("catalog-service",     "redis-cache",          1,   3,  1800),
    ("inventory-service",   "postgres-orders",      3,  10,   800),
    ("inventory-service",   "postgres-catalog",     3,  11,   400),
    # Search
    ("search-service",      "redis-cache",          1,   3,  2400),
    ("search-service",      "postgres-catalog",     3,  11,   600),
    # Reviews
    ("review-service",      "postgres-catalog",     3,  11,   200),
    # Notifications
    ("notification-svc",    "redis-cache",          1,   3,   400),
    ("notification-svc",    "twilio-sms",          82, 205,   100),
    ("notification-svc",    "sendgrid-email",      62, 185,   200),
    # Analytics / recommendations
    ("recommendation-svc",  "analytics-service",   30,  95,   100),
    ("recommendation-svc",  "redis-cache",          1,   3,   300),
    ("analytics-service",   "postgres-orders",     22,  85,    60),
    ("analytics-service",   "postgres-catalog",    20,  80,    60),
    # Auth
    ("auth-service",        "redis-cache",          1,   3,  4000),
    ("auth-service",        "postgres-orders",      3,  10,   400),
]

DEPLOYMENTS = [
    # Older stable deployments
    ("auth-service",         "v3.1.0",   48, "success"),
    ("user-service",         "v2.8.3",   36, "success"),
    ("catalog-service",      "v1.9.1",   72, "success"),
    ("inventory-service",    "v2.3.0",   24, "success"),
    ("search-service",       "v4.0.2",   96, "success"),
    ("recommendation-svc",   "v1.2.1",  120, "success"),
    ("analytics-service",    "v2.0.0",  144, "success"),
    ("notification-svc",     "v1.7.4",   48, "success"),
    ("fraud-detection-svc",  "v2.1.0",   60, "success"),
    ("shipping-service",     "v1.4.2",   72, "success"),
    ("kyc-service",          "v3.0.1",  168, "success"),
    ("coupon-service",       "v1.1.0",   96, "success"),
    ("review-service",       "v1.0.5",  200, "success"),
    ("wallet-service",       "v2.2.0",   48, "success"),
    # RECENT — correlates with degradation window
    ("payment-service",      "v2.3.0",    6, "success"),   # stable
    ("payment-service",      "v2.3.1",    2, "success"),   # ← SUSPICIOUS — 2h ago
    ("checkout-service",     "v3.2.0",    8, "success"),
    ("order-service",        "v5.1.2",   12, "success"),
    ("api-gateway",          "v7.0.1",   24, "success"),
    # Failed rollout
    ("recommendation-svc",   "v1.2.2",   18, "failed"),
]

# ---------------------------------------------------------------------------

CREATE_CONSTRAINTS = """
CREATE CONSTRAINT service_name IF NOT EXISTS
FOR (s:Service) REQUIRE s.name IS UNIQUE;
"""

CREATE_SERVICE = """
MERGE (s:Service {name: $name})
SET s.type            = $type,
    s.team            = $team,
    s.criticality     = $criticality,
    s.health_score    = $health_score,
    s.avg_latency_ms  = $avg_latency_ms,
    s.p99_latency_ms  = $p99_latency_ms,
    s.updated_at      = $updated_at
"""

CREATE_CALL = """
MATCH (a:Service {name: $caller})
MATCH (b:Service {name: $callee})
MERGE (a)-[r:CALLS]->(b)
SET r.avg_latency_ms   = $avg_ms,
    r.p99_latency_ms   = $p99_ms,
    r.requests_per_min = $rpm
"""

CREATE_DEPLOYMENT = """
MATCH (s:Service {name: $service})
MERGE (d:Deployment {id: $dep_id})
SET d.version     = $version,
    d.deployed_at = $deployed_at,
    d.status      = $status,
    d.deployed_by = "github-actions"
MERGE (s)-[:HAD_DEPLOYMENT]->(d)
"""


async def seed() -> None:
    driver = get_driver()
    async with driver.session() as session:
        await session.run(CREATE_CONSTRAINTS)
        print("✓ Constraints")

        for name, stype, team, crit, health, avg_ms, p99_ms in SERVICES:
            await session.run(CREATE_SERVICE, {
                "name": name, "type": stype, "team": team,
                "criticality": crit, "health_score": health,
                "avg_latency_ms": avg_ms, "p99_latency_ms": p99_ms,
                "updated_at": _now.isoformat(),
            })
        print(f"✓ {len(SERVICES)} services")

        for caller, callee, avg_ms, p99_ms, rpm in CALL_EDGES:
            await session.run(CREATE_CALL, {
                "caller": caller, "callee": callee,
                "avg_ms": avg_ms, "p99_ms": p99_ms, "rpm": rpm,
            })
        print(f"✓ {len(CALL_EDGES)} CALLS edges")

        for i, (svc, ver, hours_ago, status) in enumerate(DEPLOYMENTS):
            dep_time = (_now - timedelta(hours=hours_ago)).isoformat()
            await session.run(CREATE_DEPLOYMENT, {
                "service": svc, "dep_id": f"dep-{i:04d}",
                "version": ver, "deployed_at": dep_time, "status": status,
            })
        print(f"✓ {len(DEPLOYMENTS)} deployments")

    print("\n🌱 Seed complete")


async def seed_and_close() -> None:
    """Standalone entry point — seeds then closes the driver."""
    await seed()
    await close_driver()


if __name__ == "__main__":
    asyncio.run(seed_and_close())
