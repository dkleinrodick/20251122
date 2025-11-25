import asyncio
import logging
from sqlalchemy import select, update, func
from app.database import SessionLocal, init_db
from app.models import SystemSetting, RoutePair, FlightCache, Airport, FareSnapshot
from app.airports_data import AIRPORTS_LIST
from app.jobs import AutoScraper, ThreeWeekScraper

# Configure Logging to console
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.scraper")
logger.setLevel(logging.DEBUG)

async def test_scraper():
    print("--- Initializing ---")
    await init_db()
    
    async with SessionLocal() as session:
        # 0. Seed Airports if missing
        print("--- Checking Airports ---")
        res = await session.execute(select(func.count()).select_from(Airport))
        count = res.scalar()
        if count == 0:
            print(f"Seeding {len(AIRPORTS_LIST)} airports...")
            from sqlalchemy import insert
            to_insert = []
            for a in AIRPORTS_LIST:
                 to_insert.append({
                     "code": a["code"], 
                     "city_name": a["city"],
                     "timezone": a.get("timezone", "UTC"),
                     "latitude": a.get("lat"),
                     "longitude": a.get("lon")
                 })
            if to_insert:
                 await session.execute(insert(Airport).values(to_insert))
        
        # 1. Disable Proxy for local test
        print("--- Disabling Proxy ---")
        setting = await session.execute(select(SystemSetting).where(SystemSetting.key == "scraper_proxy_enabled"))
        proxy_setting = setting.scalar_one_or_none()
        if proxy_setting:
            proxy_setting.value = "false"
        else:
            session.add(SystemSetting(key="scraper_proxy_enabled", value="false"))
            
        # 2. Ensure Test Route Exists
        print("--- Checking Routes ---")
        res = await session.execute(select(RoutePair).where(RoutePair.origin == "DEN", RoutePair.destination == "MCO"))
        route = res.scalar_one_or_none()
        if not route:
            print("Adding Test Route: DEN-MCO")
            session.add(RoutePair(origin="DEN", destination="MCO", is_active=True))
        else:
            print("Ensuring Test Route is Active")
            route.is_active = True
            
        await session.commit()

    # 4. Run ThreeWeekScraper
    print("\n--- Running ThreeWeekScraper ---")
    scraper = ThreeWeekScraper()
    # Use a fresh session inside the job as designed
    await scraper.run()
    
    print("\n--- Verification ---")
    async with SessionLocal() as session:
        # Check FlightCache
        res = await session.execute(select(FlightCache).where(FlightCache.origin == "DEN", FlightCache.destination == "MCO"))
        caches = res.scalars().all()
        print(f"Found {len(caches)} FlightCache entries for DEN-MCO (Live Data).")
        
        # Check FareSnapshot
        res = await session.execute(select(FareSnapshot).where(FareSnapshot.origin == "DEN", FareSnapshot.destination == "MCO"))
        snapshots = res.scalars().all()
        print(f"Found {len(snapshots)} FareSnapshot entries for DEN-MCO (Analytics).")
        
        if snapshots:
            print("Snapshot Sample (First 3):")
            for s in snapshots[:3]:
                print(f"  - Date: {s.travel_date}, GoWild Price: {s.min_price_gowild}, Seats: {s.seats_gowild}")

if __name__ == "__main__":
    asyncio.run(test_scraper())
