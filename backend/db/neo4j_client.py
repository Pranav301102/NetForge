"""Neo4j async client singleton."""
from __future__ import annotations

import os
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver

_driver: AsyncDriver | None = None


def get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(
            os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            auth=(
                os.getenv("NEO4J_USER", "neo4j"),
                os.getenv("NEO4J_PASSWORD", "forge_password"),
            ),
        )
    return _driver


async def close_driver() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


async def run_query(cypher: str, params: dict[str, Any] | None = None) -> list[dict]:
    """Execute a Cypher query and return rows as dicts."""
    driver = get_driver()
    async with driver.session() as session:
        result = await session.run(cypher, params or {})
        records = await result.data()
        return records
