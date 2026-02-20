#!/usr/bin/env python3
"""
Standalone Datadog test — runs WITHOUT starting the server.

Tests:
  1.  Key loading from .env
  2a. Direct Datadog REST API — key validation (v1 + v2)
  2b. Direct Datadog REST API — list metrics / monitors
  3.  MCP client construction (_build_datadog_mcp_client) + tool listing

Usage:
  /opt/anaconda3/envs/myenv/bin/python test_datadog.py
"""

import shutil
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── Load .env from the backend directory ────────────────────────────────────
ENV_FILE = Path(__file__).parent / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
    print(f"✅  Loaded .env from {ENV_FILE}\n")
else:
    print(f"⚠️  No .env found at {ENV_FILE} — relying on existing environment vars\n")

# ── Grab keys ────────────────────────────────────────────────────────────────
DD_API_KEY = os.environ.get("DATADOG_API_KEY", "")
DD_APP_KEY = os.environ.get("DATADOG_APP_KEY", "")
DD_SITE    = os.environ.get("DATADOG_SITE", "datadoghq.com")

print("=" * 60)
print("STEP 1 — Key presence check")
print("=" * 60)
api_key_note = ""
if DD_API_KEY and len(DD_API_KEY) != 32:
    api_key_note = f"  ⚠️  WARNING: expected 32 hex chars, got {len(DD_API_KEY)} — key may be truncated/corrupt!"
print(f"  DATADOG_API_KEY : {'SET  (' + DD_API_KEY[:6] + '…) [len=' + str(len(DD_API_KEY)) + ']' if DD_API_KEY else '❌ NOT SET'}")
if api_key_note:
    print(api_key_note)
print(f"  DATADOG_APP_KEY : {'SET  (' + DD_APP_KEY[:6] + '…) [len=' + str(len(DD_APP_KEY)) + ']' if DD_APP_KEY else '❌ NOT SET'}")
print(f"  DATADOG_SITE    : {DD_SITE}")
print()

if not DD_API_KEY or not DD_APP_KEY:
    print("❌  Keys missing — cannot continue REST or MCP tests.")
    sys.exit(1)


# ── Helper ───────────────────────────────────────────────────────────────────
def _get(url: str, extra_headers: dict | None = None) -> tuple[int, dict | str]:
    """Make a GET request to the Datadog API, return (status_code, body)."""
    headers = {
        "DD-API-KEY": DD_API_KEY,
        "DD-APPLICATION-KEY": DD_APP_KEY,
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body


# ── REST API validation ──────────────────────────────────────────────────────
def test_rest_api() -> bool:
    print("=" * 60)
    print("STEP 2 — Direct REST API tests")
    print("=" * 60)

    base = f"https://api.{DD_SITE}"
    all_ok = True
    from_ts = int(time.time()) - 600

    tests = [
        # v1 key validation (does NOT need app key, just API key)
        ("v1 key validate",     f"{base}/api/v1/validate"),
        # v2 key validation
        ("v2 current user",     f"{base}/api/v2/current_user"),
        # Metrics query
        ("v1 active metrics",   f"{base}/api/v1/metrics?from={from_ts}"),
        # Monitors list
        ("v1 monitors (pg 0)",  f"{base}/api/v1/monitor?page=0&page_size=5"),
    ]

    for name, url in tests:
        # v1/validate only needs the API key; everything else needs app key too
        status, body = _get(url)
        if status == 200:
            if isinstance(body, dict):
                brief = {k: body[k] for k in list(body.keys())[:4]}
            elif isinstance(body, list):
                brief = f"[{len(body)} items]"
            else:
                brief = str(body)[:120]
            print(f"  ✅  {name}  (HTTP {status})")
            print(f"      {brief}")
        else:
            err = body if isinstance(body, str) else json.dumps(body)[:300]
            print(f"  ❌  {name}  (HTTP {status}): {err}")
            all_ok = False

    print()
    return all_ok


# ── MCP client test (thread-based Strands API) ───────────────────────────────
def test_mcp_client() -> bool:
    print("=" * 60)
    print("STEP 3 — Strands MCPClient construction + tool listing")
    print("         (uses npx @winor30/mcp-server-datadog)")
    print("=" * 60)

    # Add backend dir to path so agent imports resolve
    backend_dir = str(Path(__file__).parent)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    try:
        from mcp import StdioServerParameters, stdio_client
        from strands.tools.mcp import MCPClient
        print("  ✅  Imports OK (mcp, strands)")
    except ImportError as e:
        print(f"  ❌  Import failed: {e}")
        print("      Ensure myenv has `mcp` and `strands` installed.")
        return False

    # Locate npx — conda envs often lack it on their PATH
    npx_path = shutil.which("npx") or "/usr/local/bin/npx"

    # Also ensure node is on PATH for npx to use (nvm installs aren't on system PATH)
    import glob
    node_bin_candidates = (
        glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin"))
        + ["/usr/local/bin"]
    )
    node_path_extra = next(
        (p for p in node_bin_candidates if os.path.exists(os.path.join(p, "node"))),
        "",
    )
    if node_path_extra:
        npx_path = os.path.join(node_path_extra, "npx")
    current_path = os.environ.get("PATH", "")
    augmented_path = f"{node_path_extra}:{current_path}" if node_path_extra else current_path

    print(f"  npx path : {npx_path}")
    print(f"  node bin : {node_path_extra}")

    client = MCPClient(
        lambda: stdio_client(
            StdioServerParameters(
                command=npx_path,
                args=["-y", "@winor30/mcp-server-datadog"],
                env={
                    **os.environ,
                    "PATH": augmented_path,
                    "DATADOG_API_KEY": DD_API_KEY,
                    "DATADOG_APP_KEY": DD_APP_KEY,
                    "DATADOG_SITE":    DD_SITE,
                },
            )
        )
    )

    print("  Starting MCP client…  (npx may download the package on first run — allow 30s)")
    try:
        client.start()
        tools = client.list_tools_sync()
        tool_names = [t.tool_name if hasattr(t, "tool_name") else (t.name if hasattr(t, "name") else str(t)) for t in tools]
        print(f"  ✅  MCP client started — {len(tool_names)} tool(s) available:")
        for tn in tool_names:
            print(f"      • {tn}")
        return True
    except Exception as e:
        print(f"  ❌  MCP client error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            client.stop()
        except Exception:
            pass
        print()


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    rest_ok = test_rest_api()
    mcp_ok  = test_mcp_client()

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  REST API keys : {'✅ PASS' if rest_ok else '❌ FAIL — keys may be invalid/revoked or site wrong'}")
    print(f"  MCP client    : {'✅ PASS' if mcp_ok else '❌ FAIL — check npx availability or key issues above'}")
    print()

    if not rest_ok:
        print("  ⚠️  To fix REST failures:")
        print("     1. Log in to https://app.datadoghq.com → Organization Settings → API Keys")
        print("     2. Create a NEW API key and Application key")
        print("     3. Update DATADOG_API_KEY and DATADOG_APP_KEY in backend/.env")
        print()

    sys.exit(0 if (rest_ok and mcp_ok) else 1)


if __name__ == "__main__":
    main()
