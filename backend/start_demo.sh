#!/usr/bin/env bash
# ─── Forge Demo Quick-Start ───────────────────────────────────────────────
# Assumes: Python 3.11+, Neo4j running on bolt://localhost:7687
# (or use: docker run -e NEO4J_AUTH=neo4j/forge_password -p 7687:7687 -p 7474:7474 neo4j:5.26-community)

set -e
cd "$(dirname "$0")"

echo "╔═══════════════════════════════════════╗"
echo "║   Forge — Reliability Agent Demo      ║"
echo "╚═══════════════════════════════════════╝"

# Create .env if missing
if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠  Created .env from .env.example — add your credentials"
fi

# Install deps
if [ ! -d .venv ]; then
  echo "→ Creating virtualenv…"
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

echo ""
echo "→ Starting FastAPI on http://localhost:8000"
echo "→ Demo UI: http://localhost:8000/demo"
echo "→ API docs: http://localhost:8000/docs"
echo ""
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
