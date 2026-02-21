# NetForge

Autonomous microservice observability and remediation platform. A dual-model AI agent watches your service graph, detects anomalies, remediates failures, and continuously learns patterns — all in real time.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  React Frontend (D3 force graph + CopilotKit chat)          │
│  Tabs: Agent · Insights · Scaling                           │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP / SSE
┌────────────────────▼────────────────────────────────────────┐
│  FastAPI Backend (port 8000)                                 │
│  /api/graph        /api/insights     /api/cluster            │
│  /api/agent        /api/network-test /copilotkit             │
└──────┬──────────────────────────┬───────────────────────────┘
       │                          │
┌──────▼──────┐          ┌────────▼────────────────────────┐
│  Neo4j      │          │  Agent Layer                     │
│  Service    │          │  ┌─────────────────────────────┐ │
│  graph +    │          │  │  Claude (Bedrock)            │ │
│  topology   │          │  │  Main orchestrator           │ │
└─────────────┘          │  │  → Neo4j · Datadog · AWS    │ │
                         │  └──────────┬──────────────────┘ │
                         │             │ fire-and-forget     │
                         │  ┌──────────▼──────────────────┐ │
                         │  │  MiniMax M2.5 (background)  │ │
                         │  │  Deep pattern analysis       │ │
                         │  └──────────┬──────────────────┘ │
                         │             │                     │
                         │  ┌──────────▼──────────────────┐ │
                         │  │  Memory Store (JSON)         │ │
                         │  │  Insights · Patterns         │ │
                         │  │  Baselines · History         │ │
                         │  └─────────────────────────────┘ │
                         │                                   │
                         │  ┌─────────────────────────────┐ │
                         │  │  Network Testing Agent       │ │
                         │  │  Reads memory → derives      │ │
                         │  │  strategies → runs httpx     │ │
                         │  │  tests → p50/p95/p99         │ │
                         │  └─────────────────────────────┘ │
                         │                                   │
                         │  ┌─────────────────────────────┐ │
                         │  │  MAPE-K Cluster Coordinator  │ │
                         │  │  Auto-scales agent replicas  │ │
                         │  │  Monitor→Analyze→Plan→Exec   │ │
                         │  └─────────────────────────────┘ │
                         └───────────────────────────────────┘
```

---

## Features

### Agent Tab
- **CopilotKit chat** — talk directly to the Claude orchestrator
- **Live activity feed** — every tool call, insight stored, and remediation logged in real time
- **TestSprite validation panel** — endpoint health check results after each scale event

### Insights Tab
- **Persistent memory** — agent accumulates insights and patterns across sessions
- **Severity-ranked feed** — critical → high → medium → low, with ACK/RESOLVE actions
- **Pattern detection** — latency spikes, cascade risk, periodic overload, dependency bottlenecks
- **MAPE-K cluster panel** — live replica status, CPU bars, scale event log

### Scaling Tab
- **Instance cards** — live CPU/task/service-assignment for every running agent replica
- **Scale event timeline** — full history of spawn/kill events with reasons
- **TestSprite validation results** — per-endpoint latency table after each scale
- **Network Testing Agent** — see below
- **Scale report summary** — aggregate stats: ups, downs, max instances, validation pass rate

---

## Network Testing Agent

The network testing agent reads the **persistent memory store** (insights + patterns) and derives test strategies tailored to what the agent has already learned about your services.

### How it works

```
Memory (insights + patterns)
         │
         ▼
  generate_strategies()
         │
  ┌──────┴────────────────────────────────────┐
  │ Strategy types derived from memory:        │
  │                                            │
  │  health_sweep     — always included        │
  │    HTTP GET all core endpoints, 2xx check  │
  │                                            │
  │  latency_probe    — from latency insights  │
  │    10 sequential requests → p50/p95/p99    │
  │                                            │
  │  load_burst       — from overload insights │
  │    20 concurrent requests, error rate      │
  │                                            │
  │  cascade_sim      — from cascade patterns  │
  │    Sequential hop-by-hop probe to find     │
  │    where failure propagation starts        │
  │                                            │
  │  dependency_chain — from bottleneck pats   │
  │    Walk dependency order, assert each hop  │
  └────────────────────────────────────────────┘
         │
         ▼
  run_network_tests()   ← POST /api/network-test/run
         │
         ▼
  NetworkTestReport
  ├── overall_status (passed / partial / failed)
  ├── per-strategy results (p50/p95/p99, error_rate%)
  └── plain-English recommendations
```

### Network testing strategies (research-backed)

Based on current best practices for microservice resilience testing:

| Strategy | What it tests | Failure signal |
|----------|--------------|----------------|
| **Health sweep** | All endpoints reachable, 2xx, <2s | Any non-2xx or timeout |
| **Latency probe** | p99 < 1000ms, p95 < 500ms | SLO breach at p95/p99 |
| **Load burst** | 20 concurrent reqs, <5% error rate | >5% errors or p95 > 800ms |
| **Cascade simulation** | Downstream hop chain survives upstream failure | Any link breaking the chain |
| **Dependency chain** | Each service dependency is reachable in order | Broken hop = blast radius found |

Informed by:
- [Microservices Testing Strategies (TestKube)](https://testkube.io/blog/cloud-native-microservices-testing-strategies)
- [Chaos Engineering Best Practices (Steadybit)](https://steadybit.com/blog/chaos-experiments/)
- [Chaos Testing Guide (Katalon)](https://katalon.com/resources-center/blog/chaos-testing-a-complete-guide)

### API

```
GET  /api/network-test/strategies   # list strategies derived from current memory
POST /api/network-test/run          # execute all strategies, stream results
GET  /api/network-test/results      # most recent report
```

---

## Quick Start

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm start          # proxies /api/* → localhost:8000
```

### Environment variables (`backend/.env`)

| Variable | Purpose |
|----------|---------|
| `NEO4J_URI` | Bolt URI for the service graph DB |
| `NEO4J_USER` / `NEO4J_PASSWORD` | Neo4j credentials |
| `BEDROCK_MODEL_ID` | Claude model on AWS Bedrock (orchestrator) |
| `AWS_REGION` | AWS region for Bedrock |
| `MINIMAX_API` | MiniMax M2.5 API key (background analysis) |
| `DATADOG_API_KEY` / `DATADOG_APP_KEY` | Datadog REST + MCP tools |
| `DEMO_MODE` | `true` = realistic fake data, no real AWS/Datadog calls |

---

## Polling behaviour

All background polls implement:
- **Burst/rest cycle** — active for 30 s, rest for 60 s, repeat
- **Page-visibility guard** — pauses when browser tab is hidden
- **In-flight guard** — no overlapping concurrent requests
- **`Promise.allSettled`** — one slow endpoint never drops the others
- **Chunked streaming** — `/api/graph/`, `/api/cluster/report`, `/api/cluster/validations` all stream JSON in 4 KB chunks to avoid ECONNRESET

---

## Agent memory schema

```json
{
  "services": {
    "<service-name>": {
      "baseline_metrics": { "p99_latency_ms": 0, "health_score": 100 },
      "patterns": [{ "type": "latency_spike", "confidence": 0.85 }],
      "insights": [{ "severity": "high", "status": "open", "recommendation": "..." }]
    }
  },
  "global_patterns": [{ "type": "cascade_failure", "services_involved": [] }],
  "analysis_history": []
}
```
