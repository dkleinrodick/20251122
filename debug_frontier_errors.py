import asyncio
import logging
import json
from app.scraper import FrontierClient, ProxyManager
from app.database import SessionLocal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def debug_errors():
    async with SessionLocal() as session:
        mgr = ProxyManager(session)
        await mgr.load_proxies()
        proxy = await mgr.get_next_proxy()
        logger.info(f"Using proxy: {proxy}")
        
        client = FrontierClient(proxy=proxy, timeout=20.0)
        try:
            logger.info("Authenticating...")
            await client.authenticate()
            logger.info("Auth successful.")
            
            # Test 1: ORD-ORD (Same origin/dest - Definitely Invalid)
            logger.info("\n--- Test 1: ORD-ORD (Invalid) ---")
            try:
                await client.search("ORD", "ORD", "2025-11-28")
                logger.info("Result: 200 OK")
            except Exception as e:
                logger.info(f"Result: {e}")
                if hasattr(e, 'response'):
                    logger.info(f"Response Body: {e.response.text}")

            # Test 2: DEN-MCO (Valid Route)
            logger.info("\n--- Test 2: DEN-MCO (Valid) ---")
            try:
                res = await client.search("DEN", "MCO", "2025-11-28")
                logger.info("Result: 200 OK")
                # logger.info(json.dumps(res, indent=2)[:500]) # First 500 chars
            except Exception as e:
                logger.info(f"Result: {e}")

            # Test 3: DEN-JFK (Likely Invalid Direct Route for Frontier? Or maybe valid with stops?)
            # Let's try a pair that definitely doesn't exist. 
            # TTN-SFO (Trenton to SF - Frontier flies TTN, but maybe not to SFO)
            logger.info("\n--- Test 3: TTN-SFO (Likely Invalid) ---")
            try:
                await client.search("TTN", "SFO", "2025-11-28")
                logger.info("Result: 200 OK")
            except Exception as e:
                logger.info(f"Result: {e}")
                if hasattr(e, 'response'):
                    logger.info(f"Response Body: {e.response.text}")

        finally:
            await client.close()

if __name__ == "__main__":
    asyncio.run(debug_errors())
