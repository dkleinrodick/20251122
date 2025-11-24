import asyncio
import logging
from app.scraper import ScraperEngine
from app.database import SessionLocal
from datetime import datetime
import pytz

# Setup logging
logging.basicConfig(level=logging.DEBUG)

async def debug_today():
    origin = "DEN"
    dest = "ORD"
    
    # Calculate "Today"
    now_utc = datetime.now(pytz.UTC)
    date = now_utc.strftime("%Y-%m-%d")
    
    print(f"Testing Scraper for {origin}->{dest} on {date}")
    
    engine = ScraperEngine()
    async with SessionLocal() as session:
        # Force refresh = True to bypass cache and hit API
        result = await engine.perform_search(origin, dest, date, session, force_refresh=True)
        
        if "error" in result:
            print(f"❌ Error: {result['error']}")
        else:
            flights = result.get("flights", [])
            print(f"✅ Success! Found {len(flights)} flights.")
            for f in flights:
                print(f" - {f['flightNumber']} | {f['departure']} | ${f['price']}")

if __name__ == "__main__":
    asyncio.run(debug_today())
