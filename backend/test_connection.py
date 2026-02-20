"""
Connection test for all Forge backend services.

Tests:
1. Environment variables loaded correctly
2. Claude (Bedrock) orchestrator connectivity
3. MiniMax M2.5 (LiteLLM) background model connectivity
4. Neo4j graph database connectivity
5. Full analyze_service flow (Claude + MiniMax background)
"""
import asyncio
import json
import os
import sys
import time

from dotenv import load_dotenv

# Load from .env explicitly
load_dotenv(".env")


def test_env_vars():
    """Test 1: Verify all required environment variables are loaded."""
    print("=" * 60)
    print("TEST 1: Environment Variables")
    print("=" * 60)

    checks = {
        "MINIMAX_API": os.getenv("MINIMAX_API"),
        "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID"),
        "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY"),
        "AWS_SESSION_TOKEN": os.getenv("AWS_SESSION_TOKEN"),
        "AWS_DEFAULT_REGION": os.getenv("AWS_DEFAULT_REGION"),
        "BEDROCK_MODEL_ID": os.getenv("BEDROCK_MODEL_ID"),
        "NEO4J_URI": os.getenv("NEO4J_URI"),
        "DATADOG_API_KEY": os.getenv("DATADOG_API_KEY"),
        "DEMO_MODE": os.getenv("DEMO_MODE"),
    }

    all_ok = True
    for key, val in checks.items():
        status = "✓ SET" if val else "✗ NOT SET"
        # Show the value for non-sensitive keys
        display = val if key in ("BEDROCK_MODEL_ID", "AWS_DEFAULT_REGION", "DEMO_MODE") else None
        extra = f" = {display}" if display else ""
        print(f"  {status}  {key}{extra}")
        if not val and key not in ("AWS_SESSION_TOKEN",):
            all_ok = False

    print(f"\n  Result: {'PASS ✅' if all_ok else 'PARTIAL ⚠️'}\n")
    return all_ok


def test_claude_bedrock():
    """Test 2: Verify Bedrock Claude orchestrator connectivity."""
    print("=" * 60)
    print("TEST 2: Claude (Bedrock) Orchestrator")
    print("=" * 60)

    try:
        from strands.models.bedrock import BedrockModel
        from strands import Agent

        model_id = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0")
        region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-west-2"))
        print(f"  Model: {model_id}")
        print(f"  Region: {region}")

        model = BedrockModel(
            model_id=model_id,
            region_name=region,
            temperature=0.1,
            max_tokens=100,
        )
        agent = Agent(model=model, system_prompt="Reply concisely.", tools=[])

        start = time.time()
        result = agent("Say exactly: BEDROCK OK")
        elapsed = time.time() - start

        response = str(result).strip()
        print(f"  Response: {response[:100]}")
        print(f"  Latency: {elapsed:.1f}s")
        print(f"\n  Result: PASS ✅\n")
        return True

    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")
        print(f"\n  Result: FAIL ❌\n")
        return False


def test_minimax():
    """Test 3: Verify MiniMax M2.5 background model connectivity."""
    print("=" * 60)
    print("TEST 3: MiniMax M2.5 (Background Model)")
    print("=" * 60)

    api_key = os.getenv("MINIMAX_API", "")
    if not api_key:
        print("  Skipped: MINIMAX_API not set")
        print(f"\n  Result: SKIP ⏭️\n")
        return True  # Not a failure — background model is optional

    try:
        from strands.models.litellm import LiteLLMModel
        from strands import Agent
        import re

        model = LiteLLMModel(
            client_args={
                "api_key": api_key,
                "api_base": "https://api.minimax.io/v1",
            },
            model_id="openai/MiniMax-M2.5",
            params={"temperature": 0.1, "max_tokens": 100},
        )
        agent = Agent(model=model, system_prompt="Reply concisely. No thinking tags.", tools=[])

        start = time.time()
        result = agent("Say exactly: MINIMAX OK")
        elapsed = time.time() - start

        response = re.sub(r"<think>.*?</think>", "", str(result), flags=re.DOTALL).strip()
        print(f"  Response: {response[:100]}")
        print(f"  Latency: {elapsed:.1f}s")
        print(f"\n  Result: PASS ✅\n")
        return True

    except ImportError:
        print("  Error: litellm not installed — run: pip install litellm")
        print(f"\n  Result: FAIL ❌\n")
        return False
    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")
        print(f"\n  Result: FAIL ❌\n")
        return False


async def test_neo4j():
    """Test 4: Verify Neo4j graph database connectivity."""
    print("=" * 60)
    print("TEST 4: Neo4j Graph Database")
    print("=" * 60)

    try:
        from db.neo4j_client import get_driver

        driver = get_driver()
        async with driver.session() as session:
            result = await session.run("MATCH (s:Service) RETURN s.name AS name LIMIT 10")
            records = [r async for r in result]

        if records:
            print(f"  Connected! Found {len(records)} services:")
            for r in records:
                print(f"    - {r['name']}")
        else:
            print("  Connected, but no services found in graph")

        print(f"\n  Result: PASS ✅\n")
        return True

    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")
        print(f"\n  Result: FAIL ❌\n")
        return False


async def test_full_analysis():
    """Test 5: Run full analyze_service to test the combined Claude + MiniMax flow."""
    print("=" * 60)
    print("TEST 5: Full Analysis Flow (Claude + MiniMax Background)")
    print("=" * 60)

    try:
        from agent.agent import analyze_service

        start = time.time()
        report = await analyze_service("payment-service")
        elapsed = time.time() - start

        print(f"  Service: {report.get('service')}")
        print(f"  Status: {report.get('status')}")
        print(f"  Health Score: {report.get('health_score')}")
        print(f"  Actions Taken: {len(report.get('actions_taken', []))}")
        print(f"  Summary: {report.get('chat_summary', '')[:120]}...")
        print(f"  Latency: {elapsed:.1f}s")
        print(f"\n  Result: PASS ✅\n")
        return True

    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")
        print(f"\n  Result: FAIL ❌\n")
        return False


async def main():
    print("\n🔧 Forge Backend Connection Tests\n")

    results = {}

    # Synchronous tests
    results["env_vars"] = test_env_vars()
    results["claude_bedrock"] = test_claude_bedrock()
    results["minimax"] = test_minimax()

    # Async tests
    results["neo4j"] = await test_neo4j()
    results["full_analysis"] = await test_full_analysis()

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for name, passed in results.items():
        icon = "✅" if passed else "❌"
        print(f"  {icon}  {name}")

    total_pass = sum(results.values())
    total = len(results)
    print(f"\n  {total_pass}/{total} tests passed\n")

    # Wait briefly for MiniMax background tasks to complete
    print("  Waiting 5s for background tasks to settle...")
    await asyncio.sleep(5)
    print("  Done.\n")

    return all(results.values())


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
