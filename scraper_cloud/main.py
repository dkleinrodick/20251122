import sys
import os
import asyncio
import logging
import ssl
from datetime import datetime, timedelta
import pytz

# Add parent directory to path so we can import 'app'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy import select

from app.models import RoutePair, SystemSetting, Airport
from app.scraper import ScraperEngine
from app.airports_data import AIRPORTS_LIST

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scraper_cloud")

# Database Config (Session Mode)
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable not set")
    sys.exit(1)

# Force asyncpg for Postgres
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://")
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

# Remove sslmode parameter if present (asyncpg doesn't support it in URL)
if "?sslmode=" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.split("?")[0]

# Strip whitespace just in case
DATABASE_URL = DATABASE_URL.strip()

# Configure SSL for Supabase
ssl_context = ssl.create_default_context()
# SSL verification enabled by default
connect_args = {
    "ssl": ssl_context,
    "server_settings": {
        "jit": "off"
    }
}

# Create engine with NullPool for transaction pooler compatibility
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args=connect_args,
    poolclass=NullPool  # Use NullPool to avoid prepared statement issues with transaction pooler
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)

async def get_setting(session, key, default):
    res = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
    s = res.scalar_one_or_none()
    return s.value if s else default

async def main():
    logger.info("Starting Cloud Scraper...")
    
    async with SessionLocal() as session:
        # 1. Load Settings
        auto_enabled = (await get_setting(session, "auto_scrape_enabled", "true")).lower() == "true"
        auto_interval = int(await get_setting(session, "auto_scrape_interval", "60"))
        last_auto_str = await get_setting(session, "last_auto_scrape", "2000-01-01T00:00:00")
        
        midnight_enabled = (await get_setting(session, "midnight_scrape_enabled", "true")).lower() == "true"
        midnight_window = int(await get_setting(session, "midnight_scrape_window", "15"))
        
        now_utc = datetime.utcnow()
        
        tasks_to_run = []
        run_full_scrape = False
        
        # 2. Check Full Scrape Eligibility
        if auto_enabled:
            try:
                last_run = datetime.fromisoformat(last_auto_str)
                if (now_utc - last_run).total_seconds() / 60 >= auto_interval:
                    logger.info("Auto Scrape interval reached. Queueing full scrape.")
                    run_full_scrape = True
            except:
                run_full_scrape = True

        # 3. Check Midnight Scrape Eligibility
        if midnight_enabled:
            # Group airports by timezone
            tz_map = {}
            for ap in AIRPORTS_LIST:
                tz = ap.get("timezone")
                if tz:
                    if tz not in tz_map: tz_map[tz] = []
                    tz_map[tz].append(ap["code"])
            
            midnight_targets = []
            for tz_name, codes in tz_map.items():
                try:
                    tz_obj = pytz.timezone(tz_name)
                    now_loc = datetime.now(tz_obj)
                    # Check if time is 00:00 - 00:WINDOW
                    if 0 <= now_loc.hour < 1 and 0 <= now_loc.minute < midnight_window:
                        logger.info(f"Midnight window active for {tz_name} ({now_loc.strftime('%H:%M')}). Adding {len(codes)} airports.")
                        midnight_targets.extend(codes)
                except:
                    pass
            
            if midnight_targets:
                # Find routes originating from these airports
                stmt = select(RoutePair).where(RoutePair.origin.in_(midnight_targets), RoutePair.is_active == True)
                res = await session.execute(stmt)
                m_routes = res.scalars().all()
                
                # Target date is usually "Tomorrow" relative to the local midnight
                # But safely, let's just scrape the next 2 days to be sure we catch the GoWild window
                target_date = (now_utc + timedelta(days=1)).strftime("%Y-%m-%d")
                
                for r in m_routes:
                    tasks_to_run.append({"origin": r.origin, "destination": r.destination, "date": target_date, "type": "midnight"})

        # 4. Build Task List
        if run_full_scrape:
            res = await session.execute(select(RoutePair).where(RoutePair.is_active == True))
            all_routes = res.scalars().all()
            
            # Identify International Airports
            intl_codes = {a['code'] for a in AIRPORTS_LIST if a.get('is_international')}
            
            for r in all_routes:
                # Determine window size
                window = 2
                if r.origin in intl_codes or r.destination in intl_codes:
                    window = 10
                
                for i in range(window):
                    d_str = (now_utc + timedelta(days=i)).strftime("%Y-%m-%d")
                    tasks_to_run.append({"origin": r.origin, "destination": r.destination, "date": d_str, "type": "full"})

        # If invoked manually (workflow_dispatch), force full scrape?
        # For now, let's assume manual invocation via GitHub Actions implies "Run Full Scrape" 
        # unless we pass inputs. But typically manual = "I want data now".
        # Since we can't easily detect trigger type here without env vars, 
        # let's rely on the DB state. IF the user clicked "Run" in GitHub, they likely expect a run.
        # We can check if the list is empty. If so, force full run? 
        # Or relying on 'last_auto_scrape' is safer to avoid spamming.
        # However, if the user MANUALLY runs it, they bypass the schedule.
        # Let's check GITHUB_EVENT_NAME
        is_manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        if is_manual and not run_full_scrape:
             logger.info("Manual trigger detected. Forcing full scrape.")
             run_full_scrape = True
             # Re-populate
             tasks_to_run = [] # Clear potential partials
             res = await session.execute(select(RoutePair).where(RoutePair.is_active == True))
             all_routes = res.scalars().all()
             
             # Identify International Airports (Redundant but safe if scope changes)
             intl_codes = {a['code'] for a in AIRPORTS_LIST if a.get('is_international')}

             for r in all_routes:
                # Determine window size
                window = 2
                if r.origin in intl_codes or r.destination in intl_codes:
                    window = 10
                
                for i in range(window):
                    d_str = (now_utc + timedelta(days=i)).strftime("%Y-%m-%d")
                    tasks_to_run.append({"origin": r.origin, "destination": r.destination, "date": d_str, "type": "full"})

        if not tasks_to_run:
            logger.info("No scrape tasks required at this time.")
            return

        logger.info(f"Executing {len(tasks_to_run)} flight search tasks...")

        # 5. Execute with configured concurrency
        engine_scraper = ScraperEngine()

        # Get max concurrent requests from database settings
        max_concurrent = int(await get_setting(session, "max_concurrent_requests", "5"))
        logger.info(f"Using concurrency level: {max_concurrent}")

        # Create a semaphore to control concurrency
        async with SessionLocal() as tmp_session:
            sem = await engine_scraper.get_semaphore(tmp_session)

        completed_count = 0
        total_tasks = len(tasks_to_run)

        async def scrape_single(origin, destination, date):
            nonlocal completed_count
            try:
                async with sem:
                    async with SessionLocal() as task_session:
                        # Each task gets its own session to avoid conflicts
                        await engine_scraper.perform_search(origin, destination, date, task_session, force_refresh=True)
            except Exception as e:
                logger.error(f"Scrape failed for {origin}->{destination} on {date}: {e}")
            finally:
                completed_count += 1
                if completed_count % 10 == 0:
                    logger.info(f"Progress: {completed_count}/{total_tasks} tasks completed")

        tasks = []
        for item in tasks_to_run:
            tasks.append(scrape_single(item['origin'], item['destination'], item['date']))

        await asyncio.gather(*tasks)

    # 6. Update Timestamp (only if full scrape ran) - use fresh session
    if run_full_scrape:
        async with SessionLocal() as update_session:
            res = await update_session.execute(select(SystemSetting).where(SystemSetting.key == "last_auto_scrape"))
            setting = res.scalar_one_or_none()
            if setting:
                setting.value = now_utc.isoformat()
            else:
                update_session.add(SystemSetting(key="last_auto_scrape", value=now_utc.isoformat()))
            await update_session.commit()

    logger.info("Cloud Scraper Cycle Complete.")

if __name__ == "__main__":
    asyncio.run(main())