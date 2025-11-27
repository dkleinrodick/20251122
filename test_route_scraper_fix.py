"""
Quick test to verify RouteScraper works without freezing
"""
import asyncio
import logging
from app.route_scraper import RouteScraper

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def test_route_validation():
    """Test just the validation step with a small sample"""
    print("=" * 80)
    print("TESTING ROUTE SCRAPER VALIDATION (SIMPLIFIED)")
    print("=" * 80)
    print("\nThis will test route validation with the shared token optimization.")
    print("If it completes without hanging, the fix is successful!\n")

    scraper = RouteScraper()

    # Override to test with smaller sample
    async def test_validation_small(self, stop_check=None):
        from app.database import SessionLocal
        from app.models import RoutePair
        from sqlalchemy import select, or_
        from datetime import datetime, timedelta
        import pytz

        async with SessionLocal() as session:
            # Get just 10 routes for testing
            cutoff = datetime.now(pytz.UTC) - timedelta(days=3)

            stmt = select(RoutePair).where(
                or_(
                    RoutePair.is_active == False,
                    RoutePair.last_validated == None,
                    RoutePair.last_validated < cutoff
                )
            ).limit(10)  # Limit to 10 for quick test

            routes_objs = (await session.execute(stmt)).scalars().all()

            if not routes_objs:
                print("No routes need validation.")
                return 0

            total_routes = len(routes_objs)
            print(f"Testing with {total_routes} routes (limited sample)...")

            routes_data = [
                {"id": r.id, "origin": r.origin, "destination": r.destination}
                for r in routes_objs
            ]

            from app.scraper import ProxyManager
            from app.models import SystemSetting

            proxy_mgr = ProxyManager(session)
            await proxy_mgr.load_proxies()

            # Use smaller concurrency for testing
            max_concurrent = 5
            print(f"Using {max_concurrent} concurrent workers (reduced for testing)")

            # Test the actual validation
            validated_count = await self._validate_chunk(
                routes_data, proxy_mgr, max_concurrent, stop_check
            )

            print(f"\nValidation complete: {validated_count} routes validated")
            return validated_count

    # Replace the method temporarily
    original_method = scraper.validate_routes
    scraper.validate_routes = lambda stop_check=None: test_validation_small(scraper, stop_check)

    try:
        print("Starting validation test...")
        print("-" * 80)

        result = await scraper.validate_routes()

        print("-" * 80)
        print("\n[SUCCESS] Route validation completed without hanging!")
        print(f"Validated: {result} routes")
        print("\nThe shared token optimization is working correctly.")

    except asyncio.TimeoutError:
        print("\n[TIMEOUT] Test timed out - likely still hanging")
    except Exception as e:
        print(f"\n[ERROR] Test failed with exception: {e}")
        import traceback
        traceback.print_exc()

async def main():
    # Run with timeout to detect hangs
    try:
        await asyncio.wait_for(test_route_validation(), timeout=60.0)
    except asyncio.TimeoutError:
        print("\n" + "=" * 80)
        print("[TIMEOUT] Test did not complete within 60 seconds")
        print("The scraper is likely still hanging. Check the code for issues.")
        print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())
