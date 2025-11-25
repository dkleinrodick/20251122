import asyncio
from sqlalchemy import select
from app.database import SessionLocal, init_db
from app.models import SystemSetting

DEFAULTS = {
    "scraper_worker_count": "20",
    "scraper_jitter_min": "0.1",
    "scraper_jitter_max": "5.0",
    "scraper_proxy_enabled": "true",
    "scraper_stale_minutes": "60",
    "scraper_ondemand_limit": "3",
    "scraper_midnight_window_start": "00:00",
    "scraper_midnight_window_end": "00:15",
    "scraper_3week_schedule": "03:00",
    # Scheduler enabled/disabled toggles
    "schedule_auto_enabled": "true",
    "schedule_midnight_enabled": "true",
    "schedule_weather_enabled": "true",
    "schedule_3week_enabled": "true",
    "schedule_route_sync_enabled": "true",
    # Scheduler intervals and times
    "schedule_auto_interval": "30",
    "schedule_midnight_interval": "15",
    "schedule_weather_time": "09:30",
    "schedule_3week_time": "09:00",
    "schedule_route_sync_time": "00:00",
}

async def seed_settings():
    print("Initializing DB...")
    await init_db()
    
    print("Seeding Scraper 2.0 Settings...")
    async with SessionLocal() as session:
        for key, default_val in DEFAULTS.items():
            result = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
            existing = result.scalar_one_or_none()
            
            if not existing:
                print(f"  + Adding {key} = {default_val}")
                session.add(SystemSetting(key=key, value=default_val))
            else:
                print(f"  . Skipping {key} (exists: {existing.value})")
        
        await session.commit()
    print("Done.")

if __name__ == "__main__":
    asyncio.run(seed_settings())
