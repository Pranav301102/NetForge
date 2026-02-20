#!/usr/bin/env python3
"""
Datadog API Explorer — discovers ALL available data in the connected Datadog account.

Explores:
  - Account info / current user
  - Active metrics (what's being sent in)
  - Metric metadata (types, units, descriptions)
  - Monitors (alerts, check statuses)
  - Dashboards
  - Hosts / infrastructure
  - Logs indexes & pipelines
  - Services (APM / service catalog)
  - Events
  - SLOs
  - Synthetics tests

Usage:
  /opt/anaconda3/envs/myenv/bin/python explore_datadog.py
"""

import json
import os
import sys
import time
from pathlib import Path

# ── Load .env ────────────────────────────────────────────────────────────────
ENV_FILE = Path(__file__).parent / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
    print(f"✅  Loaded .env from {ENV_FILE}\n")

DD_API_KEY = os.environ.get("DATADOG_API_KEY", "")
DD_APP_KEY = os.environ.get("DATADOG_APP_KEY", "")
DD_SITE    = os.environ.get("DATADOG_SITE", "datadoghq.com")

if not DD_API_KEY or not DD_APP_KEY:
    print("❌  Missing DATADOG_API_KEY or DATADOG_APP_KEY — cannot continue.")
    sys.exit(1)

BASE = f"https://api.{DD_SITE}"

# ── HTTP helper ───────────────────────────────────────────────────────────────
import urllib.error
import urllib.request

def get(path: str, params: str = "") -> tuple[int, dict | list | str]:
    url = f"{BASE}{path}"
    if params:
        url += ("&" if "?" in url else "?") + params
    headers = {
        "DD-API-KEY":        DD_API_KEY,
        "DD-APPLICATION-KEY": DD_APP_KEY,
        "Accept":             "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:1000]
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body


def section(title: str):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def ok(label: str, data):
    print(f"  ✅  {label}")
    if isinstance(data, dict):
        # Print key fields, skip huge nested blobs
        for k, v in list(data.items())[:8]:
            val_str = json.dumps(v)[:120] if not isinstance(v, str) else v[:120]
            print(f"       {k}: {val_str}")
    elif isinstance(data, list):
        print(f"       [{len(data)} items]")
        for item in data[:5]:
            if isinstance(item, dict):
                # Show a few key fields
                summary = {k: item[k] for k in list(item.keys())[:3]}
                print(f"         • {json.dumps(summary)[:150]}")
            else:
                print(f"         • {str(item)[:150]}")
        if len(data) > 5:
            print(f"         … and {len(data) - 5} more")
    else:
        print(f"       {str(data)[:300]}")


def fail(label: str, code: int, body):
    msg = body if isinstance(body, str) else json.dumps(body)[:200]
    print(f"  ❌  {label}  (HTTP {code}): {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Account / Auth
# ─────────────────────────────────────────────────────────────────────────────
section("1. ACCOUNT & AUTH")

status, body = get("/api/v1/validate")
if status == 200:
    ok("API key valid (v1)", body)
else:
    fail("API key valid (v1)", status, body)

status, body = get("/api/v2/current_user")
if status == 200:
    attrs = body.get("data", {}).get("attributes", body)
    ok("Current user (v2)", attrs)
else:
    fail("Current user (v2)", status, body)

status, body = get("/api/v1/org")
if status == 200:
    ok("Organization info", body.get("orgs", [body])[0] if isinstance(body, dict) else body)
else:
    fail("Organization info", status, body)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Metrics — what's actively being shipped in
# ─────────────────────────────────────────────────────────────────────────────
section("2. ACTIVE METRICS (last 10 min)")

from_ts = int(time.time()) - 600
status, body = get("/api/v1/metrics", f"from={from_ts}")
if status == 200:
    metrics = body.get("metrics", []) if isinstance(body, dict) else body
    print(f"  ✅  Active metrics count: {len(metrics)}")
    if metrics:
        print("       Sample metrics (first 30):")
        for m in metrics[:30]:
            print(f"         • {m}")
        if len(metrics) > 30:
            print(f"         … and {len(metrics) - 30} more")

        # ── For each sample metric, get metadata ──
        print()
        print("  📊  Metric metadata (first 5 active metrics):")
        for m in metrics[:5]:
            s2, b2 = get(f"/api/v1/metrics/{m}")
            if s2 == 200:
                md = b2 if isinstance(b2, dict) else {}
                print(f"       [{m}]")
                for k in ("type", "unit", "per_unit", "description", "integration", "short_name"):
                    if md.get(k):
                        print(f"         {k}: {md[k]}")
            else:
                print(f"       [{m}] — metadata fetch failed (HTTP {s2})")
else:
    fail("Active metrics", status, body)


# ─────────────────────────────────────────────────────────────────────────────
# 2b. Metric search — broader discovery
# ─────────────────────────────────────────────────────────────────────────────
section("2b. METRIC SEARCH (all metrics w/ prefix '*')")

status, body = get("/api/v1/search", "q=metrics:")
if status == 200:
    results = body.get("results", {})
    all_metrics = results.get("metrics", [])
    print(f"  ✅  Total searchable metrics: {len(all_metrics)}")
    for m in all_metrics[:40]:
        print(f"    • {m}")
    if len(all_metrics) > 40:
        print(f"    … and {len(all_metrics) - 40} more")
else:
    fail("Metric search", status, body)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Infrastructure — hosts
# ─────────────────────────────────────────────────────────────────────────────
section("3. INFRASTRUCTURE — HOSTS")

status, body = get("/api/v1/hosts", "count=20")
if status == 200:
    hosts = body.get("host_list", []) if isinstance(body, dict) else []
    total = body.get("total_matching", len(hosts)) if isinstance(body, dict) else len(hosts)
    print(f"  ✅  Total hosts: {total}")
    for h in hosts[:10]:
        name    = h.get("host_name", "?")
        aliases = h.get("aliases", [])
        tags    = h.get("tags_by_source", {})
        sources = list(tags.keys())
        apps    = h.get("apps", [])
        print(f"       • {name}  aliases={aliases[:2]}  sources={sources}  apps={apps}")
else:
    fail("Hosts", status, body)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Monitors / Alerts
# ─────────────────────────────────────────────────────────────────────────────
section("4. MONITORS / ALERTS")

status, body = get("/api/v1/monitor", "page=0&page_size=20")
if status == 200:
    monitors = body if isinstance(body, list) else body.get("monitors", [])
    print(f"  ✅  Monitors found: {len(monitors)}")
    for mon in monitors[:10]:
        name   = mon.get("name", "?")
        mtype  = mon.get("type", "?")
        status_val = mon.get("overall_state", "?")
        query  = mon.get("query", "")[:80]
        print(f"       • [{mtype}] {name}  state={status_val}")
        print(f"         query: {query}")
else:
    fail("Monitors", status, body)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Dashboards
# ─────────────────────────────────────────────────────────────────────────────
section("5. DASHBOARDS")

status, body = get("/api/v1/dashboard")
if status == 200:
    dashboards = body.get("dashboards", []) if isinstance(body, dict) else body
    print(f"  ✅  Dashboards found: {len(dashboards)}")
    for d in dashboards[:10]:
        title    = d.get("title", "?")
        dash_id  = d.get("id", "?")
        layout   = d.get("layout_type", "?")
        modified = d.get("modified_at", "?")
        print(f"       • [{dash_id}] {title}  layout={layout}  modified={modified}")
else:
    fail("Dashboards", status, body)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Logs — indexes & pipelines
# ─────────────────────────────────────────────────────────────────────────────
section("6. LOGS — INDEXES & PIPELINES")

status, body = get("/api/v1/logs/indexes")
if status == 200:
    indexes = body.get("indexes", []) if isinstance(body, dict) else []
    print(f"  ✅  Log indexes: {len(indexes)}")
    for idx in indexes:
        name     = idx.get("name", "?")
        daily_gb = idx.get("dailyLimit", {}).get("value", "unlimited")
        filters  = idx.get("filter", {}).get("query", "")
        print(f"       • {name}  daily_limit={daily_gb}  filter='{filters}'")
else:
    fail("Log indexes", status, body)

status, body = get("/api/v1/logs/config/pipelines")
if status == 200:
    pipelines = body if isinstance(body, list) else body.get("pipelines", [])
    print(f"  ✅  Log pipelines: {len(pipelines)}")
    for p in pipelines[:5]:
        pname   = p.get("name", "?")
        enabled = p.get("is_enabled", "?")
        filters = p.get("filter", {}).get("query", "")
        print(f"       • {pname}  enabled={enabled}  filter={filters}")
else:
    fail("Log pipelines", status, body)


# ─────────────────────────────────────────────────────────────────────────────
# 7. APM / Services (Service Catalog)
# ─────────────────────────────────────────────────────────────────────────────
section("7. APM — SERVICES")

status, body = get("/api/v2/services/definitions")
if status == 200:
    services = body.get("data", []) if isinstance(body, dict) else body
    print(f"  ✅  Service definitions: {len(services)}")
    for svc in services[:10]:
        attrs = svc.get("attributes", {})
        print(f"       • {attrs.get('schema', {}).get('dd-service', svc.get('id', '?'))}  "
              f"type={attrs.get('schema', {}).get('type', '?')}")
else:
    fail("Service catalog", status, body)

# Also try APM services via v1 metrics time series (host tags)
status, body = get("/api/v1/tags/hosts")
if status == 200:
    tags = body.get("tags", {}) if isinstance(body, dict) else {}
    print(f"  ✅  Host tags: {len(tags)} tags")
    for tag, hosts in list(tags.items())[:10]:
        print(f"       • {tag}: {hosts[:3]}")
else:
    fail("Host tags", status, body)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Events
# ─────────────────────────────────────────────────────────────────────────────
section("8. EVENTS (last 1 hour)")

end_ts   = int(time.time())
start_ts = end_ts - 3600

status, body = get("/api/v1/events", f"start={start_ts}&end={end_ts}&count=20")
if status == 200:
    events = body.get("events", []) if isinstance(body, dict) else []
    print(f"  ✅  Events in last hour: {len(events)}")
    for ev in events[:10]:
        ev_id    = ev.get("id", "?")
        title    = ev.get("title", "?")
        source   = ev.get("source_type_name", "?")
        ts       = ev.get("date_happened", 0)
        tags     = ev.get("tags", [])[:3]
        print(f"       • [{ts}] {title}  source={source}  tags={tags}")
else:
    fail("Events", status, body)


# ─────────────────────────────────────────────────────────────────────────────
# 9. SLOs
# ─────────────────────────────────────────────────────────────────────────────
section("9. SLOs (Service Level Objectives)")

status, body = get("/api/v1/slo")
if status == 200:
    slos = body.get("data", []) if isinstance(body, dict) else []
    print(f"  ✅  SLOs found: {len(slos)}")
    for slo in slos[:5]:
        name   = slo.get("name", "?")
        stype  = slo.get("type", "?")
        target = slo.get("thresholds", [{}])[0].get("target", "?")
        print(f"       • {name}  type={stype}  target={target}%")
else:
    fail("SLOs", status, body)


# ─────────────────────────────────────────────────────────────────────────────
# 10. Synthetics
# ─────────────────────────────────────────────────────────────────────────────
section("10. SYNTHETICS TESTS")

status, body = get("/api/v1/synthetics/tests")
if status == 200:
    tests = body.get("tests", []) if isinstance(body, dict) else []
    print(f"  ✅  Synthetic tests: {len(tests)}")
    for t in tests[:5]:
        name   = t.get("name", "?")
        ttype  = t.get("type", "?")
        tstate = t.get("status", "?")
        url    = t.get("config", {}).get("request", {}).get("url", "?")
        print(f"       • [{ttype}] {name}  status={tstate}  url={url}")
else:
    fail("Synthetics", status, body)


# ─────────────────────────────────────────────────────────────────────────────
# 11. Notebooks (saved analyses)
# ─────────────────────────────────────────────────────────────────────────────
section("11. NOTEBOOKS")

status, body = get("/api/v1/notebooks")
if status == 200:
    nbs = body.get("data", []) if isinstance(body, dict) else []
    print(f"  ✅  Notebooks: {len(nbs)}")
    for nb in nbs[:5]:
        attrs = nb.get("attributes", {})
        print(f"       • {attrs.get('name', nb.get('id', '?'))}  "
              f"modified={attrs.get('modified', '?')}")
else:
    fail("Notebooks", status, body)


# ─────────────────────────────────────────────────────────────────────────────
# 12. Integrations check
# ─────────────────────────────────────────────────────────────────────────────
section("12. INSTALLED INTEGRATIONS")

for integration in ["aws", "github", "slack", "pagerduty", "kubernetes", "docker"]:
    s, b = get(f"/api/v1/integration/{integration}")
    if s == 200:
        print(f"  ✅  {integration}: INSTALLED  — {str(b)[:120]}")
    elif s == 404:
        print(f"  ⬜  {integration}: not installed")
    else:
        print(f"  ⚠️  {integration}: HTTP {s}  {str(b)[:80]}")


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
section("SUMMARY — HOW TO USE THIS DATA")

print("""
  Build on top of Datadog by using these API patterns:

  A. REAL-TIME METRICS
     GET /api/v1/metrics?from=<unix_ts>         → list active metric names
     GET /api/v1/query?from=…&to=…&query=…      → time-series values

  B. MONITORS / ALERTS (for an "ops alert" feed)
     GET /api/v1/monitor                        → all monitors + current state
     GET /api/v1/monitor/<id>/groups            → per-group status

  C. HOSTS / INFRA MAP
     GET /api/v1/hosts                          → host inventory + tag map
     GET /api/v1/tags/hosts                     → tag → [host] mapping

  D. LOGS (needs log-query scope)
     POST /api/v2/logs/events/search            → search & aggregate logs

  E. APM TRACES / SERVICES
     GET /api/v2/services/definitions           → service catalog
     GET /api/v1/search?q=metrics:<prefix>      → discover APM metrics

  F. EVENTS STREAM
     GET /api/v1/events?start=…&end=…           → infra/deploy events

  G. MCP TOOL (agent-friendly wrapper)
     The @winor30/mcp-server-datadog MCP server exposes all of the above
     as tools your Strands agent can call natively.
""")
