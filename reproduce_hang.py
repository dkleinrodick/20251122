import asyncio
import logging
from app.route_scraper import RouteScraper
from app.database import SessionLocal
from app.models import SystemSetting, RoutePair
from sqlalchemy import select

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def diagnose():
    logger.info("Starting diagnosis...")
    
    # 1. Initialize
    scraper = RouteScraper()
    scraper.job_id = "test-job-diag"
    
    # 2. Check settings
    async with SessionLocal() as session:
        res = await session.execute(select(SystemSetting).where(SystemSetting.key == "scraper_worker_count"))
        setting = res.scalar_one_or_none()
        logger.info(f"Setting scraper_worker_count: {setting.value if setting else 'None'}")
        
    # 3. Create a fake batch of routes
    batch = [
        RoutePair(origin="DEN", destination="MCO"),
        RoutePair(origin="PHL", destination="MCO"),
        RoutePair(origin="TTN", destination="MCO")
    ]
    
    logger.info(f"Testing validation on {len(batch)} routes...")
    
    # 4. Run checking logic directly (simulate what run() does inside the loop)
    # We copy the logic from route_scraper.py roughly
    
    # ... Wait, I can't call internal methods easily if they are not exposed.
    # But I can call `scraper.check_route(r)` if I mock the session/semaphore?
    # No, check_route uses internal sem.
    
    # Let's try running the scraper on a tiny subset if possible?
    # No, scraper.run() runs everything.
    
    # Let's inspect `check_route` behavior by monkeypatching?
    
    # Or better: Just replicate the `asyncio.gather` block with a timeout.
    
    async with SessionLocal() as session:
        # Re-init scraper internals
        scraper.session = session 
        await scraper.proxy_mgr.load_proxies()
        
        sem = asyncio.Semaphore(5)
        
        async def mock_check(r):
            async with sem:
                logger.info(f"Checking {r.origin}-{r.destination}...")
                try:
                    # Mock the client interaction
                    # client = FrontierClient(...)
                    # await client.authenticate()
                    logger.info("Authenticated.")
                    await asyncio.sleep(1)
                    logger.info("Searched.")
                    return {"status": "active"}
                except Exception as e:
                    logger.error(f"Error: {e}")
                    return {"status": "error"}

        tasks = [mock_check(r) for r in batch]
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        logger.info(f"Results: {results}")

if __name__ == "__main__":
    asyncio.run(diagnose())
