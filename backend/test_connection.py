import asyncio
import os
from dotenv import load_dotenv

# Load from .env explicitly
load_dotenv(".env")
print(f"Loaded MINIMAX_API: {'Yes' if os.environ.get('MINIMAX_API') else 'No'}")
print(f"Loaded AWS_ACCESS_KEY_ID: {'Yes' if os.environ.get('AWS_ACCESS_KEY_ID') else 'No'}")

from agent.agent import analyze_service

async def main():
    print("Starting connection test...")
    try:
        report = await analyze_service("payment-service")
        import json
        print(json.dumps(report, indent=2))
        print("Connection test successful.")
    except Exception as e:
        print(f"Connection test failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
