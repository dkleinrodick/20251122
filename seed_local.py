import asyncio
import logging
from sqlalchemy.future import select
from sqlalchemy import insert
from app.database import engine, init_db, SessionLocal
from app.models import SystemSetting, Airport, RoutePair
from app.airports_data import AIRPORTS_LIST
from app.scraper import ScraperEngine # Just to verify import
import httpx
import json

# Setup simple logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_data")

async def seed_settings(session):
    logger.info("Seeding default settings...")
    defaults = {
        "max_concurrent_requests": "2",
        "proxy_index": "0",
        "cache_duration_minutes": "60",
        "admin_password": "admin",
        "proxy_enabled": "true",
        "scraper_mode": "ondemand",
        "scraper_timeout": "30",
        "scraper_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "midnight_scrape_enabled": "true",
        "midnight_scrape_window": "15",
        "midnight_scrape_interval": "5",
        "auto_scrape_interval": "30",
        "auto_scrape_enabled": "true",
        "weather_scrape_time": "06:00",
        "cache_reset_enabled": "true",
        "cache_reset_days": "2",
        "cache_reset_time": "09:00",
        "last_cache_reset": "2000-01-01T00:00:00",
        "announcement_enabled": "false",
        "announcement_html": "<h3>Welcome to WildFares!</h3><p>This is a beta version.</p>",
        "debug_logging_enabled": "false"
    }
    for k, v in defaults.items():
        res = await session.execute(select(SystemSetting).where(SystemSetting.key == k))
        if not res.scalar_one_or_none():
            session.add(SystemSetting(key=k, value=str(v)))
    await session.commit()

async def seed_airports(session):
    logger.info(f"Seeding {len(AIRPORTS_LIST)} airports...")
    for entry in AIRPORTS_LIST:
        code = entry["code"]
        res = await session.execute(select(Airport).where(Airport.code == code))
        existing = res.scalar_one_or_none()
        if not existing:
            session.add(Airport(
                code=code,
                city_name=entry["city"],
                timezone=entry.get("timezone", "UTC"),
                latitude=entry.get("lat"),
                longitude=entry.get("lon")
            ))
        else:
            # Update if needed
            existing.timezone = entry.get("timezone", "UTC")
            existing.latitude = entry.get("lat")
            existing.longitude = entry.get("lon")
    await session.commit()

# Copied from app/main.py logic
async def scrape_frontier_routes(db):
    logger.info("Scraping Frontier routes...")
    from app.airports_data import AIRPORT_MAPPING # Re-import to be safe
    url = "https://flights.flyfrontier.com/en/sitemap/city-to-city-flights/page-1"
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            resp = await client.get(url)
            content = resp.text
            start_marker = '<script id="__NEXT_DATA__" type="application/json">'
            end_marker = '</script>'
            start_idx = content.find(start_marker)
            if start_idx == -1: return 0
            start_idx += len(start_marker)
            end_idx = content.find(end_marker, start_idx)
            if end_idx == -1: return 0
            data = json.loads(content[start_idx:end_idx])
            try: links = data['props']['pageProps']['sitemap']['links']
            except KeyError: return 0
            
            existing_routes = set()
            res = await db.execute(select(RoutePair))
            for r in res.scalars().all(): existing_routes.add((r.origin, r.destination))
            new_count = 0
            for link in links:
                route_name = link.get('name', '')
                if " - " in route_name:
                    parts = route_name.split(" - ")
                    if len(parts) == 2:
                        origin_name = parts[0].strip()
                        dest_name = parts[1].strip()
                        
                        # Use AIRPORT_MAPPING to find codes
                        origin_codes = AIRPORT_MAPPING.get(origin_name, [])
                        if not origin_codes and ", " in origin_name: 
                            origin_codes = AIRPORT_MAPPING.get(origin_name.split(",")[0].strip(), [])
                        
                        dest_codes = AIRPORT_MAPPING.get(dest_name, [])
                        if not dest_codes and ", " in dest_name: 
                            dest_codes = AIRPORT_MAPPING.get(dest_name.split(",")[0].strip(), [])
                        
                        for o_code in origin_codes:
                            for d_code in dest_codes:
                                if (o_code, d_code) not in existing_routes:
                                    db.add(RoutePair(origin=o_code, destination=d_code, is_active=True))
                                    new_count += 1
                                    existing_routes.add((o_code, d_code))
            await db.commit()
            logger.info(f"Found {new_count} new routes.")
            return new_count
    except Exception as e:
        logger.error(f"Error scraping routes: {e}")
        return 0

async def main():
    logger.info("Starting seeding process...")
    # Ensure tables exist
    await init_db()
    
    async with SessionLocal() as session:
        await seed_settings(session)
        await seed_airports(session)
        await scrape_frontier_routes(session)
        
    logger.info("Seeding complete!")

if __name__ == "__main__":
    asyncio.run(main())
