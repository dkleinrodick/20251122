import sys
import os
import asyncio
import logging
import ssl
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

load_dotenv()

# Add parent directory to path so we can import 'app'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy import select, delete

from app.models import RoutePair, SystemSetting, Airport, FlightCache, ScraperRun
from app.scraper import ScraperEngine
from app.airports_data import AIRPORTS_LIST
import time

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
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE
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

async def should_skip_route(session, origin, destination, date_str, days_out, cache_domestic_min, cache_intl_hours):
    """
    Check if a route should be skipped based on cache age and timezone-aware same-day logic.

    Args:
        session: Database session
        origin: Origin airport code
        destination: Destination airport code
        date_str: Travel date (YYYY-MM-DD)
        days_out: Days from now to travel date
        cache_domestic_min: Cache duration for domestic/near-term routes (minutes)
        cache_intl_hours: Cache duration for international far-term routes (hours)

    Returns:
        True if route should be skipped, False if it needs scraping
    """
    # Check if we have cached data for this route+date
    stmt = select(FlightCache).where(
        FlightCache.origin == origin,
        FlightCache.destination == destination,
        FlightCache.travel_date == date_str
    ).order_by(FlightCache.created_at.desc()).limit(1)

    res = await session.execute(stmt)
    cache_entry = res.scalar_one_or_none()

    if not cache_entry:
        return False  # No cache, must scrape

    # Get origin airport timezone
    airport_res = await session.execute(select(Airport).where(Airport.code == origin))
    airport = airport_res.scalar_one_or_none()
    origin_tz = pytz.timezone(airport.timezone if airport and airport.timezone else "UTC")

    # Get current time in origin's timezone
    now_origin_tz = datetime.now(origin_tz)

    # Get cache time in origin's timezone
    cache_time_utc = cache_entry.created_at
    if cache_time_utc.tzinfo is None:
        cache_time_utc = pytz.utc.localize(cache_time_utc)
    cache_time_origin_tz = cache_time_utc.astimezone(origin_tz)

    # Determine if we're looking at 1-2 days out (near-term) or 3-10 days out (far-term)
    is_near_term = days_out <= 2

    if is_near_term:
        # Near-term logic: Skip if cached within last X minutes AND same day in origin timezone
        time_diff_minutes = (now_origin_tz - cache_time_origin_tz).total_seconds() / 60

        # Check if it's the same calendar day in origin timezone
        same_day = (now_origin_tz.date() == cache_time_origin_tz.date())

        if same_day and time_diff_minutes < cache_domestic_min:
            logger.debug(f"Skipping {origin}->{destination} on {date_str}: Cached {time_diff_minutes:.0f}min ago (same day in {origin_tz})")
            return True
    else:
        # Far-term logic (3-10 days): Skip if cached within last X hours
        time_diff_hours = (now_origin_tz - cache_time_origin_tz).total_seconds() / 3600

        if time_diff_hours < cache_intl_hours:
            logger.debug(f"Skipping {origin}->{destination} on {date_str}: Cached {time_diff_hours:.1f}h ago (far-term)")
            return True

    return False  # Cache is stale, needs scraping

async def cleanup_old_runs(session):
    """Keep only the last 20 scraper runs"""
    # Get all runs ordered by most recent first
    stmt = select(ScraperRun).order_by(ScraperRun.started_at.desc())
    res = await session.execute(stmt)
    all_runs = res.scalars().all()

    if len(all_runs) > 20:
        # Delete runs beyond the 20 most recent
        runs_to_delete = all_runs[20:]
        for run in runs_to_delete:
            await session.delete(run)
        await session.commit()
        logger.info(f"Cleaned up {len(runs_to_delete)} old scraper runs")

async def main():
    logger.info("Starting Cloud Scraper...")
    start_time = datetime.utcnow()
    scraper_run_id = None

    # Stats tracking
    stats = {
        "total_routes": 0,
        "routes_scraped": 0,
        "routes_skipped": 0,
        "routes_failed": 0
    }

    # Create scraper run record
    async with SessionLocal() as session:
        scraper_run = ScraperRun(started_at=start_time, status="running")
        session.add(scraper_run)
        await session.commit()
        await session.refresh(scraper_run)
        scraper_run_id = scraper_run.id
        logger.info(f"Created scraper run record #{scraper_run_id}")

    try:
        async with SessionLocal() as session:
            # 1. Load Settings
            auto_enabled = (await get_setting(session, "auto_scrape_enabled", "true")).lower() == "true"
            auto_interval = int(await get_setting(session, "auto_scrape_interval", "60"))
            last_auto_str = await get_setting(session, "last_auto_scrape", "2000-01-01T00:00:00")

            midnight_enabled = (await get_setting(session, "midnight_scrape_enabled", "true")).lower() == "true"
            midnight_window = int(await get_setting(session, "midnight_scrape_window", "15"))

            # Cache duration settings
            cache_domestic_min = int(await get_setting(session, "scraper_cache_domestic_minutes", "60"))
            cache_intl_hours = int(await get_setting(session, "scraper_cache_international_hours", "12"))

            # Use US/Pacific time to determine "Today" to ensure we don't skip the current day
            # during late-night hours when UTC has already rolled over.
            now_ref = datetime.now(pytz.timezone('America/Los_Angeles'))

            tasks_to_run = []
            run_full_scrape = False

            # 2. Check Full Scrape Eligibility
            if auto_enabled:
                try:
                    last_run = datetime.fromisoformat(last_auto_str)
                    # Use UTC for interval comparison to keep it consistent
                    if (datetime.utcnow() - last_run).total_seconds() / 60 >= auto_interval:
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

                    # Target "Today" and "Tomorrow" to be safe for Midnight scraper
                    target_dates = [
                        now_ref.strftime("%Y-%m-%d"),
                        (now_ref + timedelta(days=1)).strftime("%Y-%m-%d")
                    ]

                    for r in m_routes:
                        for d_str in target_dates:
                            tasks_to_run.append({"origin": r.origin, "destination": r.destination, "date": d_str, "type": "midnight"})

            # 4. Build Task List
            if run_full_scrape:
                res = await session.execute(select(RoutePair).where(RoutePair.is_active == True))
                all_routes = res.scalars().all()

                # Identify International Airports
                intl_codes = {a['code'] for a in AIRPORTS_LIST if a.get('is_international')}

                skipped_count = 0
                for r in all_routes:
                    # Determine window size
                    window = 2
                    if r.origin in intl_codes or r.destination in intl_codes:
                        window = 10

                    for i in range(window):
                        d_str = (now_ref + timedelta(days=i)).strftime("%Y-%m-%d")

                        # Check if we should skip this route based on cache age
                        should_skip = await should_skip_route(
                            session, r.origin, r.destination, d_str, i,
                            cache_domestic_min, cache_intl_hours
                        )

                        if should_skip:
                            skipped_count += 1
                            stats["routes_skipped"] += 1
                        else:
                            tasks_to_run.append({"origin": r.origin, "destination": r.destination, "date": d_str, "type": "full"})

                if skipped_count > 0:
                    logger.info(f"Skipped {skipped_count} routes with fresh cache data")

            # If invoked manually (workflow_dispatch), force full scrape?
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

                 skipped_count = 0
                 for r in all_routes:
                    # Determine window size
                    window = 2
                    if r.origin in intl_codes or r.destination in intl_codes:
                        window = 10

                    for i in range(window):
                        d_str = (now_ref + timedelta(days=i)).strftime("%Y-%m-%d")

                        # Check if we should skip this route based on cache age
                        should_skip = await should_skip_route(
                            session, r.origin, r.destination, d_str, i,
                            cache_domestic_min, cache_intl_hours
                        )

                        if should_skip:
                            skipped_count += 1
                            stats["routes_skipped"] += 1
                        else:
                            tasks_to_run.append({"origin": r.origin, "destination": r.destination, "date": d_str, "type": "full"})

                 if skipped_count > 0:
                     logger.info(f"Skipped {skipped_count} routes with fresh cache data")

            if not tasks_to_run:
                logger.info("No scrape tasks required at this time.")
                # Update run record
                async with SessionLocal() as update_session:
                    stmt = select(ScraperRun).where(ScraperRun.id == scraper_run_id)
                    res = await update_session.execute(stmt)
                    run = res.scalar_one_or_none()
                    if run:
                        run.completed_at = datetime.utcnow()
                        run.duration_seconds = (run.completed_at - run.started_at).total_seconds()
                        run.status = "completed"
                        run.total_routes = 0
                        run.routes_scraped = 0
                        run.routes_skipped = 0
                        run.routes_failed = 0
                        await update_session.commit()
                await cleanup_old_runs(update_session)
                return

            stats["total_routes"] = len(tasks_to_run)
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
                            # Note: force_refresh is False since we already filtered stale routes
                            await engine_scraper.perform_search(origin, destination, date, task_session, force_refresh=False)
                            stats["routes_scraped"] += 1
                except Exception as e:
                    logger.error(f"Scrape failed for {origin}->{destination} on {date}: {e}")
                    stats["routes_failed"] += 1
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
                now_iso = datetime.utcnow().isoformat()
                if setting:
                    setting.value = now_iso
                else:
                    update_session.add(SystemSetting(key="last_auto_scrape", value=now_iso))
                await update_session.commit()

        logger.info("Cloud Scraper Cycle Complete.")

        # Update scraper run record with success
        async with SessionLocal() as update_session:
            stmt = select(ScraperRun).where(ScraperRun.id == scraper_run_id)
            res = await update_session.execute(stmt)
            run = res.scalar_one_or_none()
            if run:
                run.completed_at = datetime.utcnow()
                run.duration_seconds = (run.completed_at - run.started_at).total_seconds()
                run.status = "completed"
                run.total_routes = stats["total_routes"]
                run.routes_scraped = stats["routes_scraped"]
                run.routes_skipped = stats["routes_skipped"]
                run.routes_failed = stats["routes_failed"]
                await update_session.commit()
                logger.info(f"Scraper run #{scraper_run_id} completed: {stats['routes_scraped']}/{stats['total_routes']} successful, {stats['routes_skipped']} skipped, {stats['routes_failed']} failed")

            # Clean up old runs
            await cleanup_old_runs(update_session)

    except Exception as e:
        logger.error(f"Scraper run failed: {e}", exc_info=True)
        # Update run record with failure
        async with SessionLocal() as update_session:
            stmt = select(ScraperRun).where(ScraperRun.id == scraper_run_id)
            res = await update_session.execute(stmt)
            run = res.scalar_one_or_none()
            if run:
                run.completed_at = datetime.utcnow()
                run.duration_seconds = (run.completed_at - run.started_at).total_seconds()
                run.status = "failed"
                run.error_message = str(e)[:500]  # Truncate error message
                run.total_routes = stats["total_routes"]
                run.routes_scraped = stats["routes_scraped"]
                run.routes_skipped = stats["routes_skipped"]
                run.routes_failed = stats["routes_failed"]
                await update_session.commit()
        raise

if __name__ == "__main__":
    asyncio.run(main())
