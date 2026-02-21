"""
Forge — FastAPI application entry point.

Start with:
  uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from api.routes.actions_routes import router as actions_router
from api.routes.agent_routes import router as agent_router
from api.routes.demo_routes import router as demo_router
from api.routes.graph_routes import router as graph_router
from api.routes.hooks_routes import router as hooks_router
from api.routes.insights_routes import router as insights_router
from api.routes.cluster_routes import router as cluster_router
from api.routes.network_test_routes import router as network_test_router
from db.neo4j_client import close_driver, get_driver


# ---------------------------------------------------------------------------
# Lifespan — connect Neo4j on startup, close on shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm-up: verify Neo4j connection
    try:
        driver = get_driver()
        async with driver.session() as session:
            await session.run("RETURN 1")
        print("✓ Neo4j connected")
    except Exception as e:
        print(f"⚠ Neo4j not reachable: {e} — graph tools will fail")

    # Initialize persistent memory store (ensures JSON file exists)
    try:
        from memory.store import load_memory
        mem = load_memory()
        svc_count = len(mem.get("services", {}))
        ins_count = sum(len(s.get("insights", [])) for s in mem.get("services", {}).values())
        print(f"✓ Memory store loaded ({svc_count} services, {ins_count} insights)")
    except Exception as e:
        print(f"⚠ Memory store init warning: {e}")

    # Initialize cluster coordinator
    try:
        from cluster.coordinator import get_coordinator
        coord = get_coordinator()
        # Try to seed coordinator with service list from Neo4j
        try:
            driver = get_driver()
            async with driver.session() as session:
                result = await session.run("MATCH (s:Service) RETURN s.name AS name")
                services = [record["name"] async for record in result]
                if services:
                    coord.set_services(services)
        except Exception:
            pass
        print(f"✓ Cluster coordinator started ({coord.get_status()['total_replicas']} replicas)")
    except Exception as e:
        print(f"⚠ Cluster coordinator warning: {e}")

    yield
    await close_driver()
    print("✓ Neo4j driver closed")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Forge Reliability Agent",
    description="Autonomous microservice observability and remediation platform",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow the Vite frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.getenv("FRONTEND_URL", "http://localhost:5173"),
        "http://localhost:3000",  # alternate dev port
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(agent_router)
app.include_router(graph_router)
app.include_router(actions_router)
app.include_router(demo_router)
app.include_router(hooks_router)
app.include_router(insights_router)
app.include_router(cluster_router)
app.include_router(network_test_router)


# ---------------------------------------------------------------------------
# CopilotKit backend endpoint
# CopilotKit's CopilotRuntime expects a POST at /copilotkit that:
# 1. Receives a JSON body with { messages, ... }
# 2. Streams back SSE in the AG-UI / CopilotKit wire format
#
# Since we're using Strands (not LangGraph), we implement the protocol
# manually — this is the simplest compatible subset for chat.
# ---------------------------------------------------------------------------

# Agent manifest returned by both /copilotkit?info=true and /copilotkit/info
_AGENT_MANIFEST = {
    "version": "1.0.0",
    "agents": {
        "default": {
            "name": "default",
            "className": "default",
            "description": "Forge reliability agent — powered by AWS Strands + MiniMax",
        }
    },
    "audioFileTranscriptionEnabled": False
}


@app.get("/copilotkit/info")
async def copilotkit_info():
    """
    Legacy discovery endpoint (kept for backward compat).
    """
    return _AGENT_MANIFEST


@app.get("/copilotkit")
async def copilotkit_get(info: bool = False):
    """
    CopilotKit v1.51+ agent discovery endpoint.
    The frontend SDK calls GET /copilotkit?info=true on startup to find
    registered agents. We expose a single 'default' agent backed by Strands.
    """
    return _AGENT_MANIFEST


@app.post("/copilotkit")
async def copilotkit_endpoint(request: Request):
    """
    Minimal CopilotKit-compatible SSE endpoint.
    Bridges CopilotKit frontend ↔ Strands agent.
    """
    from agent.agent import chat_with_agent
    from agent.activity_log import log_activity

    body = await request.json()
    messages = body.get("messages", [])

    # Extract the latest user message
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                # CopilotKit sometimes sends content as parts array
                user_message = " ".join(
                    part.get("text", "") for part in content if part.get("type") == "text"
                )
            else:
                user_message = content
            break

    if not user_message:
        user_message = "Summarize the current state of all services."

    # Context injected by useCopilotReadable on the frontend
    context = body.get("context", {})

    # Log the incoming request
    log_activity(
        "analysis",
        f"Agent invoked: {user_message[:100]}",
        detail=user_message[:300],
        source="minimax",
    )

    async def ag_ui_stream():
        """Emit CopilotKit-compatible AG-UI protocol events."""
        # 1. Run started
        yield f"data: {json.dumps({'type': 'RunStarted'})}\n\n"
        yield f"data: {json.dumps({'type': 'TextMessageStart', 'messageId': 'msg-1', 'role': 'assistant'})}\n\n"

        full_response = ""
        try:
            async for chunk in chat_with_agent(user_message, context):
                full_response += chunk
                chunk_size = 20
                for i in range(0, len(chunk), chunk_size):
                    piece = chunk[i:i + chunk_size]
                    yield f"data: {json.dumps({'type': 'TextMessageContent', 'messageId': 'msg-1', 'delta': piece})}\n\n"
        except Exception as exc:
            # Graceful fallback so the stream doesn't crash the SSE connection
            import traceback
            err_msg = f"[Agent unavailable — running in demo mode. Error: {type(exc).__name__}: {exc}]"
            print(f"[CopilotKit] stream error: {traceback.format_exc()}")
            yield f"data: {json.dumps({'type': 'TextMessageContent', 'messageId': 'msg-1', 'delta': err_msg})}\n\n"
            log_activity("error", f"Agent error: {type(exc).__name__}", detail=str(exc), source="system")

        # Log completion with summary
        summary = full_response[:200] if full_response else "No response generated"
        log_activity(
            "analysis",
            f"Agent completed analysis",
            detail=summary,
            source="minimax",
        )

        yield f"data: {json.dumps({'type': 'TextMessageEnd', 'messageId': 'msg-1'})}\n\n"
        yield f"data: {json.dumps({'type': 'RunFinished'})}\n\n"

    return StreamingResponse(
        ag_ui_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/demo", include_in_schema=False)
async def demo_ui():
    """Serve the self-contained demo HTML page."""
    html_path = Path(__file__).parent.parent / "demo.html"
    return FileResponse(html_path, media_type="text/html")


@app.get("/")
async def root():
    return {
        "service": "forge-backend",
        "status": "ok",
        "demo": "/demo",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
