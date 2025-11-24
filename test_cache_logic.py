import asyncio
import logging
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv
from sqlalchemy.future import select
from app.database import SessionLocal
from app.models import FlightCache, SystemSetting, Airport
from app.scraper import ScraperEngine

# Load env
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug_cache")

async def test_logic():
    print("--- Testing Cache Validation Logic ---")
    
    engine = ScraperEngine()
    
    async with SessionLocal() as session:
        # 1. Ensure Settings exist
        print("Checking settings...")
        res = await session.execute(select(SystemSetting).where(SystemSetting.key == "cache_duration_minutes"))
        setting = res.scalar_one_or_none()
        duration = int(setting.value) if setting else 60
        print(f"Cache Duration Setting: {duration} minutes")

        # 2. Ensure Airport exists
        print("Checking Airport (DEN)...")
        res = await session.execute(select(Airport).where(Airport.code == "DEN"))
        den = res.scalar_one_or_none()
        if not den:
            print("❌ DEN not found in DB. Seeding required for test.")
            return
        print(f"Airport found: {den.code} ({den.timezone})")

        # 3. Create Test Cases
        now_utc = datetime.now(pytz.UTC)
        today_str = now_utc.strftime("%Y-%m-%d")
        
        # Case A: Fresh Cache (10 mins old)
        entry_fresh = FlightCache(
            origin="DEN",
            destination="ORD",
            travel_date=today_str,
            created_at=now_utc - timedelta(minutes=10),
            data=b""
        )
        
        # Case B: "Yesterday's" Cache (created 23 hours ago, still within 24h if limit was high? No, default is 120m)
        # Let's force the setting to 24 hours temporarily for this test logic if needed, 
        # or just test the specific scenario: 
        # User ran scraper at 11:50 PM. Now it is 12:10 AM.
        # Created at: 20 mins ago. 
        # Calendar day: Changed.
        
        entry_midnight_cross = FlightCache(
            origin="DEN",
            destination="ORD",
            travel_date=today_str, # Flight is "Today"
            created_at=now_utc - timedelta(minutes=20), # Created 20 mins ago
            data=b""
        )
        
        # Mock a timezone shift: 
        # If DEN is UTC-7. 
        # Current UTC: 07:05 (12:05 AM DEN).
        # Created UTC: 06:45 (11:45 PM DEN - Yesterday).
        
        print(f"\nTest Case: Cache created 20 mins ago.")
        valid = await engine.is_cache_valid(session, entry_midnight_cross)
        print(f"Result: {'✅ VALID' if valid else '❌ INVALID'}")
        
        if not valid:
            print("Debug: Why invalid?")
            # Re-run logic steps
            age = now_utc - entry_midnight_cross.created_at
            print(f"Age: {age} (Limit: {duration} mins)")
            if age > timedelta(minutes=duration):
                print("-> Expired by Age limit.")
            else:
                print("-> Expired by Date/Timezone logic.")

        # Case C: Actually Stale
        entry_stale = FlightCache(
            origin="DEN",
            destination="ORD",
            travel_date=today_str,
            created_at=now_utc - timedelta(minutes=duration + 10),
            data=b""
        )
        print(f"\nTest Case: Cache created {duration+10} mins ago.")
        valid = await engine.is_cache_valid(session, entry_stale)
        print(f"Result: {'✅ VALID' if valid else '❌ INVALID (Correct)'}")

if __name__ == "__main__":
    asyncio.run(test_logic())
