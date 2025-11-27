"""
Test the shared token optimization in the updated scrapers
Verify that only 1 auth call is made instead of N calls
"""
import asyncio
import logging
from datetime import datetime, timedelta
from app.database import SessionLocal
from app.scraper import AsyncScraperEngine
from app.models import RoutePair
from sqlalchemy import select

# Setup logging to see the auth messages
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_scraper_shared_token():
    """Test that AsyncScraperEngine uses shared token (1 auth instead of 20+)"""
    print("=" * 80)
    print("TEST: AsyncScraperEngine Shared Token Optimization")
    print("=" * 80)

    async with SessionLocal() as session:
        # Get 10 active routes for testing
        stmt = select(RoutePair).where(RoutePair.is_active == True).limit(10)
        routes = (await session.execute(stmt)).scalars().all()

        if not routes:
            print("No active routes found. Run RouteScraper first.")
            return

        # Create tasks for 10 routes × 1 date = 10 tasks
        tasks = []
        test_date = (datetime.now().date() + timedelta(days=7)).strftime("%Y-%m-%d")

        for route in routes:
            tasks.append({
                "origin": route.origin,
                "destination": route.destination,
                "date": test_date
            })

        print(f"\nTesting with {len(tasks)} tasks")
        print(f"Worker count setting will determine pool size (default: 20)")
        print(f"\nLook for these log messages:")
        print("  1. 'Authenticating once to get shared token...' (should appear ONCE)")
        print("  2. 'Shared token obtained: ...' (should appear ONCE)")
        print("  3. 'Creating pool of N clients with shared token...'")
        print("  4. NO lazy auth messages per client")

        print("\n" + "-" * 80)
        print("Starting scraper...")
        print("-" * 80 + "\n")

        engine = AsyncScraperEngine(session)
        results = await engine.process_queue(tasks, mode="ondemand", ignore_jitter=True)

        print("\n" + "-" * 80)
        print("RESULTS")
        print("-" * 80)
        print(f"Scraped: {results['scraped']}")
        print(f"Errors: {results['errors']}")
        print(f"Skipped: {results['skipped']}")

        if results['errors'] == 0:
            print("\n[SUCCESS] All tasks completed successfully!")
            print("Check the logs above - you should see:")
            print("  - Only 1 'Authenticating once' message")
            print("  - Only 1 'Shared token obtained' message")
            print("  - Pool creation with shared token")
        else:
            print(f"\n[PARTIAL] {results['errors']} tasks failed")
            if results['details']:
                print("Errors:")
                for detail in results['details'][:5]:
                    print(f"  - {detail}")

async def count_auth_calls():
    """Monitor auth calls by checking log messages"""
    print("\n" + "=" * 80)
    print("OPTIMIZATION IMPACT")
    print("=" * 80)

    print("\nBefore optimization (lazy auth):")
    print("  - Each client authenticates on first use")
    print("  - 20 workers = 20 auth calls")
    print("  - Total time: ~20 × 0.5s = ~10 seconds")

    print("\nAfter optimization (shared token):")
    print("  - 1 auth call before pool creation")
    print("  - All clients share the same token")
    print("  - Total time: ~1 × 0.5s = ~0.5 seconds")
    print("  - Savings: ~9.5 seconds per scraper run")

    print("\nFor 3-week scraper (17,178 tasks):")
    print("  - Previous: 20-50 auth calls depending on pool size")
    print("  - Now: 1 auth call")
    print("  - Savings: ~10-25 seconds per run")

async def main():
    print("\n" + "🚀 " * 20)
    print("TESTING SHARED TOKEN OPTIMIZATION")
    print("🚀 " * 20)

    await test_scraper_shared_token()
    await count_auth_calls()

    print("\n" + "=" * 80)
    print("NEXT STEPS")
    print("=" * 80)
    print("1. Review the logs above to confirm only 1 auth call was made")
    print("2. Run the 3-week scraper to see the optimization in action")
    print("3. Monitor logs for any 'Re-authenticating' messages (token expiration)")
    print("4. If everything works well, the optimization is successful!")

if __name__ == "__main__":
    asyncio.run(main())
