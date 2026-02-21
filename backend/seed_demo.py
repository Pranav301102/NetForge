"""
seed_demo.py — Rapid demo environment setup for NetForge.

Seeds Neo4j with a realistic Shopist e-commerce service topology
(matching the real Datadog AKS account), then:
  1. Wipes stale service data
  2. Creates a rich service dependency graph
  3. Injects a degraded scenario (payment-service under stress)
  4. Pre-populates memory with historical incidents

Run with:
  /opt/anaconda3/envs/myenv/bin/python3 seed_demo.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── Neo4j ─────────────────────────────────────────────────────────────────────
from neo4j import AsyncGraphDatabase

NEO4J_URI  = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD", "forge_password")


_driver = None

def get_driver():
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    return _driver

async def run(q, params=None):
    async with get_driver().session() as session:
        result = await session.run(q, params or {})
        return [r.data() async for r in result]

async def close_driver():
    global _driver
    if _driver:
        await _driver.close()
        _driver = None


# ── Service definitions (matches Shopist/AKS Datadog telemetry) ───────────────
SERVICES = [
    # name,                    type,       team,         criticality, health, p99,  avg,  cpu, mem
    ("api-gateway",            "gateway",  "platform",   "critical",   88,    310,  120,   22,  38),
    ("frontend-web",           "service",  "frontend",   "high",       92,    180,   70,   15,  30),
    ("auth-service",           "service",  "security",   "critical",   85,    260,   95,   38,  55),
    ("order-service",          "service",  "commerce",   "critical",   42,   2100,  840,   87,  79),
    ("payment-service",        "service",  "commerce",   "critical",   31,   3400, 1360,   94,  88),
    ("inventory-service",      "service",  "commerce",   "high",       67,    820,  310,   61,  70),
    ("checkout-service",       "service",  "commerce",   "high",       55,   1400,  560,   74,  66),
    ("notification-service",   "service",  "platform",   "medium",     90,    140,   55,   12,  25),
    ("recommendation-engine",  "service",  "ml",         "medium",     78,    450,  180,   55,  72),
    ("search-service",         "service",  "commerce",   "high",       82,    340,  130,   41,  50),
    ("cart-service",           "service",  "commerce",   "high",       88,    195,   75,   28,  44),
    ("user-profile-service",   "service",  "platform",   "medium",     91,    160,   60,   19,  35),
    ("postgres-orders",        "database", "data",       "critical",   72,    680,  270,   45,  68),
    ("postgres-users",         "database", "data",       "high",       95,    120,   45,   18,  52),
    ("redis-cache",            "cache",    "platform",   "high",       58,    890,  350,   62,  81),
    ("cassandra-catalog",      "database", "data",       "high",       76,    540,  210,   49,  63),
    ("mongodb-sessions",       "database", "data",       "medium",     89,    200,   80,   22,  48),
    ("kafka-events",           "queue",    "platform",   "critical",   94,    110,   42,   16,  38),
    ("s3-assets",              "storage",  "platform",   "low",        99,     60,   22,    5,  10),
    ("payment-gateway-ext",    "service",  "external",   "critical",   30,   4100, 1640,    0,   0),
]

# ── Dependency edges ──────────────────────────────────────────────────────────
# (source, target, avg_latency_ms, p99_latency_ms, rpm)
EDGES = [
    ("api-gateway",           "auth-service",           15,  45, 4300),
    ("api-gateway",           "frontend-web",            8,  22, 4300),
    ("api-gateway",           "order-service",           22,  80, 1200),
    ("api-gateway",           "search-service",          18,  55, 2100),
    ("api-gateway",           "cart-service",            12,  38, 1800),
    ("frontend-web",          "recommendation-engine",   45, 180,  800),
    ("order-service",         "payment-service",         95, 380,  420),
    ("order-service",         "inventory-service",       38, 150,  900),
    ("order-service",         "notification-service",    22,  80, 1100),
    ("order-service",         "postgres-orders",         55, 220, 3200),
    ("order-service",         "kafka-events",            12,  38, 2800),
    ("order-service",         "redis-cache",             18,  65, 4100),
    ("payment-service",       "payment-gateway-ext",    480,1920,  420),
    ("payment-service",       "postgres-orders",         62, 250,  840),
    ("checkout-service",      "payment-service",        120, 480,  380),
    ("checkout-service",      "cart-service",            18,  65,  760),
    ("checkout-service",      "inventory-service",       35, 140,  560),
    ("cart-service",          "redis-cache",             10,  32, 6200),
    ("cart-service",          "cassandra-catalog",       42, 165,  980),
    ("inventory-service",     "postgres-orders",         48, 190, 1400),
    ("inventory-service",     "kafka-events",            14,  45,  800),
    ("auth-service",          "redis-cache",              8,  28, 8500),
    ("auth-service",          "postgres-users",          22,  85, 4200),
    ("user-profile-service",  "postgres-users",          28, 110, 1600),
    ("user-profile-service",  "mongodb-sessions",        18,  70,  900),
    ("search-service",        "cassandra-catalog",       38, 148, 2800),
    ("search-service",        "redis-cache",             12,  42, 3600),
    ("recommendation-engine", "cassandra-catalog",       55, 215,  400),
    ("notification-service",  "kafka-events",            10,  32, 1100),
    ("order-service",         "s3-assets",               25,  90,  220),
]


async def seed():
    print("🌱 Seeding Neo4j with Shopist service topology...")

    # 1. Clear existing
    await run("MATCH (n) DETACH DELETE n")
    print("  ✓ Cleared existing graph")

    # 2. Create Service nodes
    for (name, stype, team, criticality, health, p99, avg, cpu, mem) in SERVICES:
        await run("""
            CREATE (s:Service {
                name:               $name,
                type:               $type,
                team:               $team,
                criticality:        $criticality,
                health_score:       $health,
                p99_latency_ms:     $p99,
                avg_latency_ms:     $avg,
                cpu_usage_percent:  $cpu,
                mem_usage_percent:  $mem,
                data_source:        'seed',
                updated_at:         toString(datetime())
            })
        """, dict(name=name, type=stype, team=team, criticality=criticality,
                  health=health, p99=p99, avg=avg, cpu=cpu, mem=mem))

    print(f"  ✓ Created {len(SERVICES)} service nodes")

    # 3. Create CALLS edges
    for (src, tgt, avg_l, p99_l, rpm) in EDGES:
        await run("""
            MATCH (a:Service {name: $src}), (b:Service {name: $tgt})
            CREATE (a)-[:CALLS {
                avg_latency_ms:  $avg,
                p99_latency_ms:  $p99,
                requests_per_min: $rpm
            }]->(b)
        """, dict(src=src, tgt=tgt, avg=avg_l, p99=p99_l, rpm=rpm))

    print(f"  ✓ Created {len(EDGES)} dependency edges")

    # 4. Add historical deployments (for the timeline view)
    deployments = [
        ("payment-service",  "v3.2.1", "success",  "-3h"),
        ("order-service",    "v5.1.0", "success",  "-5h"),
        ("checkout-service", "v2.8.3", "success",  "-6h"),
        ("auth-service",     "v4.0.1", "success",  "-8h"),
        ("redis-cache",      "v7.0.15","success",  "-10h"),
    ]
    for svc, version, status, offset_h in deployments:
        await run("""
            MATCH (s:Service {name: $service})
            CREATE (d:Deployment {
                id: randomUUID(),
                version: $version,
                status: $status,
                deployed_at: toString(datetime() - duration({hours: $hours})),
                deployed_by: 'ci-pipeline'
            })
            CREATE (s)-[:HAD_DEPLOYMENT]->(d)
        """, dict(service=svc, version=version, status=status,
                  hours=int(offset_h.replace("-","").replace("h",""))))

    print(f"  ✓ Created {len(deployments)} historical deployments")

    # 5. Pre-populate memory with historical incidents
    from memory.store import load_memory, save_memory, add_insight, add_pattern, add_global_pattern

    # Critical cascade incident — payment-gateway causing downstream failures
    add_insight("payment-service", {
        "category": "reliability",
        "severity": "critical",
        "title": "External payment gateway causing cascade failures",
        "insight": "payment-gateway-ext p99 has been at 4100ms for the last 3 hours, 8.2x above baseline. This is causing payment-service to queue requests, exhausting its thread pool (94% CPU). order-service and checkout-service are experiencing secondary degradation as their payment calls time out.",
        "evidence": json.dumps({"p99_latency_ms": 4100, "cpu_usage_percent": 94, "health_score": 31, "external_dep": "payment-gateway-ext"}),
        "recommendation": "Implement circuit breaker on payment-gateway-ext with 1500ms timeout. Enable async payment processing with retry queue via kafka-events.",
    })
    add_insight("order-service", {
        "category": "performance",
        "severity": "high",
        "title": "P99 latency 10.5x above baseline — upstream dependency cascade",
        "insight": "order-service p99 at 2100ms vs 200ms baseline. Root cause: payment-service (downstream) is backed up due to payment-gateway-ext degradation. 87% CPU utilization indicates thread pool saturation from waiting on slow payment calls.",
        "evidence": json.dumps({"p99_latency_ms": 2100, "cpu_usage_percent": 87, "health_score": 42}),
        "recommendation": "Scale order-service from 2 to 4 replicas. Add 800ms timeout on payment-service calls with graceful degradation (accept order, process payment async).",
    })
    add_insight("redis-cache", {
        "category": "reliability",
        "severity": "high",
        "title": "Redis memory at 81% — OOMKill risk within 2 hours",
        "insight": "redis.mem.used trending upward at 5.2MB/min. At current rate, will hit maxmemory in ~110 minutes. auth-service and cart-service (both heavy Redis users) will experience eviction storms, causing auth failures and cart data loss.",
        "evidence": json.dumps({"mem_usage_percent": 81, "health_score": 58, "trend": "increasing"}),
        "recommendation": "Increase TTL on session keys from 86400s to 3600s. Set maxmemory-policy allkeys-lru. Schedule MEMORY PURGE during next low-traffic window (3-4am UTC).",
    })
    add_insight("cassandra-catalog", {
        "category": "performance",
        "severity": "medium",
        "title": "Cassandra read latency elevated — affects search and cart",
        "insight": "cassandra.latency.95th_percentile at 540ms (baseline 120ms). Compaction backlog detected. Search-service and recommendation-engine are the primary consumers — both showing secondary degradation.",
        "evidence": json.dumps({"p99_latency_ms": 540, "health_score": 76}),
        "recommendation": "Trigger manual compaction during off-peak. Increase compaction throughput_mb_per_sec from 16 to 64. Consider read replica for recommendation-engine queries.",
    })

    add_pattern("payment-service", {
        "type": "cascade_failure",
        "description": "[Historical] External payment gateway degrades → payment-service CPU spikes → order-service and checkout-service queue → user-facing 502s. Observed 3 times in last 14 days. Each incident lasted 45-90 minutes.",
        "confidence": 0.95,
        "recommendation": "Circuit breaker is the only durable fix. Async payment queue via Kafka is the longer-term solution.",
    })
    add_pattern("redis-cache", {
        "type": "periodic_overload",
        "description": "[Historical] Redis memory spikes every Monday 9-11am UTC (peak traffic + weekly analytics job competing for memory). Pattern detected across 8 observations.",
        "confidence": 0.88,
        "recommendation": "Pre-emptively flush analytics cache keys before Monday 9am. Schedule analytics jobs to run Saturday 3am instead.",
    })
    add_global_pattern({
        "type": "deployment_risk",
        "services_involved": ["payment-service", "order-service", "checkout-service"],
        "description": "Deployments to payment-service within 30 minutes of order-service have caused 2 of the last 3 incidents. Services share postgres-orders connection pool, creating implicit coupling during startup.",
        "mitigation": "Enforce 30-minute deployment gap between payment-service and order-service. Add canary gate requiring 5-minute p99 stability before full rollout.",
    })

    print("  ✓ Pre-loaded memory with 4 insights + 2 patterns + 1 global pattern")

    # Summary
    nodes = await run("MATCH (s:Service) RETURN count(s) as n")
    edges = await run("MATCH ()-[r:CALLS]->() RETURN count(r) as n")
    print(f"\n✅ Done! Graph: {nodes[0]['n']} services, {edges[0]['n']} edges")
    print("\nReady for demo:")
    print("  • payment-service (health: 31 — CRITICAL, cascade from payment-gateway-ext)")
    print("  • order-service   (health: 42 — CRITICAL, upstream dependency)")
    print("  • redis-cache     (health: 58 — DEGRADED, memory pressure)")
    print("  • 17 other services ranging healthy to degraded")
    print("\nNext steps:")
    print("  1. Start backend: uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload")
    print("  2. Run Datadog sync: curl -X POST localhost:8000/api/hooks/datadog-sync -H 'Content-Type: application/json' -d '{}'")
    print("  3. Generate insights: curl -X POST localhost:8000/api/insights/generate")
    print("  4. Open frontend: http://localhost:3000")


if __name__ == "__main__":
    async def main():
        try:
            await seed()
        finally:
            await close_driver()
    asyncio.run(main())
