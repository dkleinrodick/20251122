from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from contextlib import asynccontextmanager
import asyncio
import logging
from datetime import datetime, timedelta
import pytz
import httpx

from app.models import SystemSetting, Airport, RoutePair, FlightCache, WeatherData
from app.scraper import ScraperEngine
from app.airports_data import AIRPORTS_LIST

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

# Scheduler-specific DB connection to avoid event loop sharing issues
@asynccontextmanager
async def SchedulerDB():
    # Create a fresh engine per job execution to ensure it binds to the current thread's loop
    engine = create_async_engine("sqlite+aiosqlite:///./gowild.db", echo=False)
    try:
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            yield session
    finally:
        await engine.dispose()

# Global Scraper Status
SCRAPER_STATUS = {
    "running": False,
    "routes_processed": 0,
    "total_routes": 0,
    "flights_added": 0,
    "flights_deleted": 0,
    "errors": 0,
    "current_route": "",
    "last_updated": None
}

# --- Helper Functions ---

async def get_setting(key: str, default: str = None):
    async with SchedulerDB() as session:
        res = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
        setting = res.scalar_one_or_none()
        return setting.value if setting else default

async def get_enabled_routes(session: AsyncSession):
    res = await session.execute(select(RoutePair).where(RoutePair.is_active == True))
    return res.scalars().all()

# --- Jobs ---

async def run_auto_scraper(force: bool = False):
    """
    Runs periodically. Triggered by Scheduler based on 'auto_scrape_interval'.
    """
    # Note: We no longer check 'scraper_mode' here. 
    # If the user sets an interval > 0, the scheduler runs this job.
    # We also don't need to check 'last_run' vs 'interval' because the scheduler handles the timing.

    logger.info(f"Starting Auto Scraper Job (Force={force})...")
    
    # Update Last Run (for reference)
    async with SchedulerDB() as session:
        stmt = select(SystemSetting).where(SystemSetting.key == "last_auto_scrape")
        res = await session.execute(stmt)
        setting = res.scalar_one_or_none()
        now_iso = datetime.utcnow().isoformat()
        if setting:
            setting.value = now_iso
        else:
            session.add(SystemSetting(key="last_auto_scrape", value=now_iso))
        await session.commit()
    
    # Reset Status
    global SCRAPER_STATUS
    
    async with SchedulerDB() as session:
        routes_orm = await get_enabled_routes(session)
        routes = [{"origin": r.origin, "destination": r.destination} for r in routes_orm]
        logger.info(f"Auto Scraper: Found {len(routes)} enabled routes to process.")
        
        # Calculate total tasks (Routes * Dates)
        today = datetime.utcnow().date()
        dates = [today, today + timedelta(days=1)]
        total_tasks = len(routes) * len(dates)
        SCRAPER_STATUS["total_routes"] = total_tasks
        
        engine = ScraperEngine()
        
        # Get Max Concurrent Requests
        max_conn_setting = await get_setting("max_concurrent_requests", "2")
        max_conn = int(max_conn_setting)
        logger.debug(f"Auto Scraper: Running with max_concurrency={max_conn}")
        sem = asyncio.Semaphore(max_conn)
        
        tasks = []
        
        async def scrape_task(route, date_obj):
            async with sem:
                date_str = date_obj.strftime("%Y-%m-%d")
                origin = route['origin']
                dest = route['destination']
                
                # Update Status (Last Started)
                SCRAPER_STATUS["current_route"] = f"{origin} -> {dest} ({date_str})"
                
                try:
                    logger.debug(f"Auto-scraping {origin}-{dest} for {date_str}")
                    # Need a new session per task? Or share? 
                    # Sharing async session across concurrent tasks can be tricky if not careful.
                    # perform_search uses the session to read cache and delete/add.
                    # SQLAlchemy AsyncSession is NOT thread-safe, and using it concurrently in tasks is risky.
                    # Safer to create a new session per task or serialize DB ops.
                    # Given `perform_search` logic, it does multiple awaits.
                    # Let's spawn a fresh session for each task to be robust.
                    async with SchedulerDB() as task_session:
                        result = await engine.perform_search(origin, dest, date_str, task_session, force_refresh=True)
                    
                    if isinstance(result, dict):
                        stats = result.get("stats", {})
                        SCRAPER_STATUS["flights_added"] += stats.get("added", 0)
                        SCRAPER_STATUS["flights_deleted"] += stats.get("deleted", 0)
                        SCRAPER_STATUS["errors"] += stats.get("errors", 0)
                except Exception as e:
                    logger.error(f"Auto-scrape failed for {origin}-{dest}: {e}")
                    SCRAPER_STATUS["errors"] += 1
                finally:
                    SCRAPER_STATUS["routes_processed"] += 1
                    SCRAPER_STATUS["last_updated"] = datetime.utcnow().isoformat()

        # Create tasks
        for route in routes:
            for date_obj in dates:
                tasks.append(scrape_task(route, date_obj))
        
        # Run concurrently
        if tasks:
            await asyncio.gather(*tasks)
                    
    SCRAPER_STATUS["running"] = False
    SCRAPER_STATUS["current_route"] = "Idle"
    logger.info("Auto Scraper Job Completed")

async def run_midnight_scraper():
    """
    Runs frequently to check if any timezone has just passed midnight.
    If so, scrapes routes originating from that timezone for the new day (Day+1).
    """
    enabled = await get_setting("midnight_scrape_enabled", "true")
    if enabled.lower() != "true":
        return

    window_minutes = int(await get_setting("midnight_scrape_window", "15"))
    interval_minutes = int(await get_setting("midnight_scrape_interval", "5")) # Run every X mins within window
    
    # Group airports by timezone
    tz_map = {}
    for ap in AIRPORTS_LIST:
        tz = ap.get("timezone")
        if tz not in tz_map:
            tz_map[tz] = []
        tz_map[tz].append(ap["code"])
        
    async with SchedulerDB() as session:
        engine = ScraperEngine()
        
        # Debug: Log the time for the first timezone to verify scheduler alignment
        first_tz = True
        
        for tz_name, airports in tz_map.items():
            try:
                tz = pytz.timezone(tz_name)
                now_loc = datetime.now(tz)
                
                if first_tz:
                    logger.debug(f"Midnight Scraper Tick: Checking {tz_name} at {now_loc.strftime('%H:%M')}. Window={window_minutes}, Interval={interval_minutes}")
                    first_tz = False
                
                # Check if within the midnight window (e.g., 00:00 to 00:15)
                if 0 <= now_loc.hour < 1 and 0 <= now_loc.minute < window_minutes:
                    
                    # Check interval frequency (e.g., only run at 0, 5, 10...)
                    # Prevent division by zero
                    if interval_minutes > 0 and now_loc.minute % interval_minutes == 0:
                        logger.info(f"Midnight detected in {tz_name} (Time: {now_loc.strftime('%H:%M')}). Scraping {len(airports)} airports.")
                        
                        # Target Date: Tomorrow (relative to the timezone that just hit midnight)
                        # GoWild rule: Booking opens at midnight for the NEXT day (Day+1)
                        target_date = (now_loc + timedelta(days=1)).strftime("%Y-%m-%d")
                        
                        # Find routes originating from these airports
                        stmt = select(RoutePair).where(RoutePair.origin.in_(airports), RoutePair.is_active == True)
                        res = await session.execute(stmt)
                        routes_orm = res.scalars().all()
                        # Detach data
                        routes = [{"origin": r.origin, "destination": r.destination} for r in routes_orm]
                        
                        # Update global status to reflect activity
                        global SCRAPER_STATUS
                        SCRAPER_STATUS["running"] = True
                        SCRAPER_STATUS["total_routes"] += len(routes)
                        SCRAPER_STATUS["last_updated"] = datetime.utcnow().isoformat()
                        
                        # Get Max Concurrent Requests
                        max_conn_setting = await get_setting("max_concurrent_requests", "2")
                        max_conn = int(max_conn_setting)
                        sem = asyncio.Semaphore(max_conn)
                        
                        tasks = []
                        
                        async def midnight_task(route):
                            async with sem:
                                SCRAPER_STATUS["current_route"] = f"Midnight: {route['origin']} -> {route['destination']}"
                                try:
                                    logger.debug(f"Midnight scrape: {route['origin']}-{route['destination']} for {target_date}")
                                    async with SchedulerDB() as task_session:
                                        result = await engine.perform_search(route['origin'], route['destination'], target_date, task_session, force_refresh=True)
                                    
                                    if isinstance(result, dict):
                                        stats = result.get("stats", {})
                                        SCRAPER_STATUS["flights_added"] += stats.get("added", 0)
                                        SCRAPER_STATUS["flights_deleted"] += stats.get("deleted", 0)
                                        SCRAPER_STATUS["errors"] += stats.get("errors", 0)
                                        SCRAPER_STATUS["last_updated"] = datetime.utcnow().isoformat()
                                except Exception as e:
                                    logger.error(f"Midnight scrape failed for {route['origin']}-{route['destination']}: {e}")
                                    SCRAPER_STATUS["errors"] += 1

                        for route in routes:
                            tasks.append(midnight_task(route))
                            
                        if tasks:
                            await asyncio.gather(*tasks)
            except Exception as e:
                logger.error(f"Error checking timezone {tz_name}: {e}")
                
        # Reset status if not running main scraper? 
        # Ideally we merge status or track separately. For now, just let it linger or reset if we knew we finished.
        # Since this runs fast, we can set running=False after loop if main scraper isn't running.
        if not scheduler.get_job("auto_scraper"): # Rough check, or just leave it.
             pass

async def run_route_validation():
    """
    Periodically validates routes to see if they are still returning 403s or errors.
    Disable routes that fail consistently.
    """
    logger.info("Starting Route Validation...")
    async with SchedulerDB() as session:
        # Get routes with high error counts or check all?
        # Let's check all for now, or maybe just those with error_count > 0
        res = await session.execute(select(RoutePair))
        routes = res.scalars().all()
        
        engine = ScraperEngine()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        for route in routes:
            # Simple check: try to search. If 403/404/500, increment error.
            # The perform_search method already updates route.error_count on failure.
            # So we just need to trigger a search.
            await engine.perform_search(route.origin, route.destination, today, session, force_refresh=True)
            
    logger.info("Route Validation Completed")

async def update_weather_data():
    """
    Fetches weather for all active destinations using Open-Meteo.
    Ensures coverage for current day, following day, and next 5-7 days.
    """
    logger.info("Updating Weather Data...")
    async with SchedulerDB() as session:
        async with httpx.AsyncClient() as client:
            for idx, entry in enumerate(AIRPORTS_LIST):
                code = entry["code"]
                lat = entry.get("lat")
                lon = entry.get("lon")
                
                if not lat or not lon: continue
                
                try:
                    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=weathercode,temperature_2m_max&timezone=auto"
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        daily = data.get("daily", {})
                        
                        times = daily.get("time", [])
                        codes = daily.get("weathercode", [])
                        temps = daily.get("temperature_2m_max", [])
                        
                        if idx == 0:
                            logger.debug(f"Weather Sample ({code}): Covering {len(times)} days from {times[0]} to {times[-1]}")

                        for i, date_str in enumerate(times):
                            # Store in DB
                            # Upsert logic needed
                            stmt = select(WeatherData).where(WeatherData.airport_code == code, WeatherData.date == date_str)
                            res = await session.execute(stmt)
                            wd = res.scalar_one_or_none()
                            
                            if not wd:
                                wd = WeatherData(airport_code=code, date=date_str)
                                session.add(wd)
                            
                            wd.condition_code = codes[i]
                            wd.temp_high = temps[i]
                            wd.updated_at = datetime.utcnow()
                        
                        # Commit every 10 airports to batch? Or just at end?
                        # Just commit every airport to be safe/simple
                        await session.commit()
                except Exception as e:
                    logger.error(f"Weather update failed for {code}: {e}")
                    
    logger.info("Weather Update Completed")

async def run_cache_cleanup():
    """
    Clears old cache entries.
    """
    async with SchedulerDB() as session:
        # For now, just hard delete anything older than 48 hours?
        # Or verify expiry based on settings.
        pass # ScraperEngine checks validity on read, but we should purge periodically.

async def run_periodic_cache_reset():
    """
    Periodic Full Reset: Clears entire cache and triggers re-scrape.
    Frequency defined by `cache_reset_days`.
    """
    enabled = await get_setting("cache_reset_enabled", "true")
    if enabled.lower() != "true":
        return

    # Check if due
    last_reset_str = await get_setting("last_cache_reset", "2000-01-01T00:00:00")
    try:
        last_reset = datetime.fromisoformat(last_reset_str)
    except:
        last_reset = datetime(2000, 1, 1)
        
    days_interval = int(await get_setting("cache_reset_days", "2"))
    
    # Simple check: Is (now - last_reset) >= days?
    # NOTE: CronTrigger handles the "at 4 AM" part. We just check if enough days passed.
    if datetime.utcnow() - last_reset >= timedelta(days=days_interval):
        logger.info("Starting Periodic Cache Reset & Full Scrape...")
        
        async with SchedulerDB() as session:
            # 1. Clear Cache
            await session.execute(delete(FlightCache))
            
            # 2. Update Timestamp
            # Upsert setting
            stmt = select(SystemSetting).where(SystemSetting.key == "last_cache_reset")
            res = await session.execute(stmt)
            setting = res.scalar_one_or_none()
            now_iso = datetime.utcnow().isoformat()
            
            if setting:
                setting.value = now_iso
            else:
                session.add(SystemSetting(key="last_cache_reset", value=now_iso))
            
            await session.commit()
            logger.info("Cache cleared.")
            
        # 3. Trigger Full Scrape
        # We reuse the auto scraper logic which scrapes all enabled routes for today/tomorrow
        await run_auto_scraper()
        logger.info("Periodic Reset & Scrape Initiated.")

# --- Wrappers for Sync Scheduler ---

def _run_async_job(async_func):
    try:
        loop = asyncio.get_running_loop()
        # We are in the main event loop, schedule task
        loop.create_task(async_func())
    except RuntimeError:
        # We are in a scheduler worker thread, run blocking
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(async_func())
        finally:
            loop.close()

async def reschedule_auto_scraper_job():
    """
    Reads the 'auto_scrape_interval' setting and schedules the auto scraper job.
    If interval <= 0, the job is disabled.
    """
    interval_str = await get_setting("auto_scrape_interval", "30")
    try:
        interval = int(interval_str)
    except:
        interval = 30
        
    enabled_str = await get_setting("auto_scrape_enabled", "true")
    is_enabled = enabled_str.lower() == "true"
    
    # Remove existing job first to ensure clean state
    if scheduler.get_job("auto_scraper"):
        scheduler.remove_job("auto_scraper")
    
    if interval > 0 and is_enabled:
        scheduler.add_job(
            lambda: _run_async_job(lambda: run_auto_scraper(force=False)), 
            IntervalTrigger(minutes=interval), 
            id="auto_scraper", 
            replace_existing=True
        )
        logger.info(f"Auto Scraper scheduled every {interval} minutes.")
    else:
        logger.info("Auto Scraper disabled (User Setting or Interval <= 0).")

# --- Init ---

def start_scheduler():
    # Add jobs
    # 1. Auto Scraper (Dynamic Interval)
    # Trigger initial schedule
    _run_async_job(reschedule_auto_scraper_job)
    
    # 2. Midnight Scraper (Every 1 min to catch the configured intervals)
    scheduler.add_job(lambda: _run_async_job(run_midnight_scraper), IntervalTrigger(minutes=1), id="midnight_scraper", replace_existing=True)
    
    # 3. Weather (Dynamic)
    _run_async_job(reschedule_weather_job)
    
    # 4. Periodic Cache Reset (Daily check at 09:00 UTC / 04:00 CST)
    scheduler.add_job(
        lambda: _run_async_job(run_periodic_cache_reset), 
        CronTrigger(hour=9, minute=0), 
        id="cache_reset", 
        replace_existing=True
    )
    
    scheduler.start()
    logger.info("Scheduler started.")

async def reschedule_weather_job():
    """
    Reads the 'weather_scrape_time' setting and schedules the weather job accordingly.
    Format expected: "HH:MM" (24-hour). Default: "06:00"
    """
    time_str = await get_setting("weather_scrape_time", "06:00")
    try:
        hour, minute = map(int, time_str.split(':'))
    except ValueError:
        logger.error(f"Invalid weather_scrape_time format: {time_str}. Fallback to 06:00")
        hour, minute = 6, 0

    # Remove existing if any
    if scheduler.get_job("weather_updater"):
        scheduler.remove_job("weather_updater")
        
    scheduler.add_job(
        lambda: _run_async_job(update_weather_data), 
        CronTrigger(hour=hour, minute=minute), 
        id="weather_updater", 
        replace_existing=True
    )
    logger.info(f"Weather job scheduled for {hour:02d}:{minute:02d} UTC daily.")