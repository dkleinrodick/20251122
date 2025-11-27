import asyncio
import logging
from app.database import SessionLocal
from app.models import SystemSetting, Proxy
from app.scraper import ProxyManager
from sqlalchemy import select

# Mock Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def reproduce():
    logger.info("Starting reproduction...")
    
    # 1. Create a shared session (like RouteScraper does)
    session = SessionLocal()
    
    # 2. Initialize Manager
    mgr = ProxyManager(session)
    await mgr.load_proxies()
    logger.info(f"Loaded {len(mgr.proxies)} proxies. Current index: {mgr.current_index}")
    
    # 3. Spawn concurrent tasks that all hit the DB via mgr
    async def worker(i):
        logger.info(f"Worker {i} starting...")
        try:
            # This calls session.execute internally
            p = await mgr.get_next_proxy()
            logger.info(f"Worker {i} got proxy: {p}")
        except Exception as e:
            logger.error(f"Worker {i} failed: {e}")

    tasks = [worker(i) for i in range(20)]
    
    # 4. Run concurrent
    # If session is not thread-safe/task-safe for interleaved execute(), this might hang or error.
    await asyncio.gather(*tasks)
    
    logger.info("Done.")
    await session.close()

if __name__ == "__main__":
    asyncio.run(reproduce())
