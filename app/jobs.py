import asyncio
import logging
import traceback
import uuid
from datetime import datetime, timedelta
import pytz
from sqlalchemy import select, func, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models import RoutePair, Airport, FlightCache, SystemSetting, ScraperRun, FareSnapshot
from app.scraper import AsyncScraperEngine
from app.job_manager import JOBS, check_stop, update_job, complete_job, register_job
from app.compression import decompress_data

logger = logging.getLogger(__name__)

class BaseJob:
    def __init__(self):
        self.engine = None
        self.job_name = "Unknown"
        self.heartbeat_id = None
        self.mode = "manual"
        self.job_id = None
        self.final_status = None

    async def get_engine(self, session):
        if not self.engine:
            self.engine = AsyncScraperEngine(session)
        return self.engine

    async def get_setting(self, session, key: str, default: str) -> str:
        res = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
        setting = res.scalar_one_or_none()
        return setting.value if setting else default

    async def run(self, heartbeat_id=None, mode=None, job_id=None):
        """
        Wrapper to handle database logging for the job.
        """
        if heartbeat_id is not None:
            self.heartbeat_id = heartbeat_id
        if mode is not None:
            self.mode = mode
        
        self.job_id = job_id or str(uuid.uuid4())
        
        # If not already registered (e.g. triggered via heartbeat vs manual API), register it
        if self.job_id not in JOBS:
            register_job(self.job_id)
            update_job(self.job_id, message=f"Starting {self.job_name}...")

        run_id = None
        start_time = datetime.now(pytz.UTC)

        # Check if subclass already identified a run to resume (e.g., ThreeWeekScraper)
        if hasattr(self, 'current_run_id') and self.current_run_id:
            run_id = self.current_run_id
            logger.info(f"Resuming existing run #{run_id}")
        else:
            # Create new 'Running' log
            async with SessionLocal() as session:
                run_log = ScraperRun(
                    job_type=self.job_name,
                    heartbeat_id=self.heartbeat_id,
                    mode=self.mode,
                    status="running",
                    started_at=start_time
                )
                session.add(run_log)
                await session.commit()
                await session.refresh(run_log)
                run_id = run_log.id

                # Store run_id in subclass if it has the attribute
                if hasattr(self, 'current_run_id'):
                    self.current_run_id = run_id

        stats = {"scraped": 0, "skipped": 0, "errors": 0, "total": 0, "details": []}
        status = "completed"
        error_msg = None

        try:
            # Check cancellation before starting
            if check_stop(self.job_id):
                status = "cancelled"
                update_job(self.job_id, status="cancelled", message="Cancelled before start")
            else:
                # Execute subclass logic
                # Pass stop_check callback
                job_stats = await self._execute_job(stop_check=lambda: check_stop(self.job_id))
                if job_stats:
                    stats.update(job_stats)
                
                if check_stop(self.job_id):
                    status = "cancelled"
                    update_job(self.job_id, status="cancelled", message="Cancelled during execution")
                elif self.final_status:
                    status = self.final_status
                    update_job(self.job_id, status=status, message=f"Job paused ({status}). Scraped: {stats.get('scraped',0)}")
                else:
                    complete_job(self.job_id, message=f"Completed. Scraped: {stats.get('scraped',0)}")

        except Exception as e:
            status = "failed"
            error_msg = str(e)
            update_job(self.job_id, status="failed", message=f"Error: {str(e)}")
            logger.error(f"Job {self.job_name} failed: {e}")
            traceback.print_exc()

        end_time = datetime.now(pytz.UTC)
        duration = (end_time - start_time).total_seconds()

        # Update Log
        async with SessionLocal() as session:
            stmt = select(ScraperRun).where(ScraperRun.id == run_id)
            res = await session.execute(stmt)
            run_log = res.scalar_one_or_none()

            if run_log:
                run_log.status = status
                run_log.completed_at = end_time
                run_log.duration_seconds = duration
                run_log.error_message = error_msg

                # Map stats to columns
                run_log.total_routes = stats.get("total", 0)
                run_log.routes_scraped = stats.get("scraped", 0)
                run_log.routes_skipped = stats.get("skipped", 0)
                run_log.routes_failed = stats.get("errors", 0)
                run_log.details = stats.get("details")

                await session.commit()

    async def _execute_job(self, stop_check=None):
        raise NotImplementedError()

class AutoScraper(BaseJob):
    def __init__(self):
        super().__init__()
        self.job_name = "AutoScraper"

    async def _execute_job(self, stop_check=None):
        logger.info("Starting AutoScraper Job...")
        stats = {"scraped": 0, "skipped": 0, "errors": 0, "total": 0}

        tasks = []
        stale_mins = 60
        
        # Step 1: Gather Data (Short-lived session)
        async with SessionLocal() as session:
            stale_mins = int(await self.get_setting(session, "scraper_stale_minutes", "60"))

            res = await session.execute(select(RoutePair).where(RoutePair.is_active == True))
            routes = res.scalars().all()

            airports_res = await session.execute(select(Airport))
            airports = {a.code: a for a in airports_res.scalars().all()}

            # Build list of all route-date combinations to check
            route_date_combos = []
            for route in routes:
                origin_ap = airports.get(route.origin)
                if not origin_ap: continue

                tz = pytz.timezone(origin_ap.timezone or "UTC")
                now_local = datetime.now(tz)
                dates_to_check = [
                    now_local.strftime("%Y-%m-%d"),
                    (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
                ]

                for date_str in dates_to_check:
                    route_date_combos.append({
                        "origin": route.origin,
                        "destination": route.destination,
                        "date": date_str,
                        "tz": tz,
                        "now_local": now_local
                    })

            stats["total"] = len(route_date_combos)

            # Build sets of origins, destinations, and dates we care about
            origins = {combo["origin"] for combo in route_date_combos}
            destinations = {combo["destination"] for combo in route_date_combos}
            dates = {combo["date"] for combo in route_date_combos}

            # Fetch ONLY relevant cache records in a SINGLE query
            if origins and destinations and dates:
                cache_res = await session.execute(
                    select(FlightCache).where(
                        and_(
                            FlightCache.origin.in_(origins),
                            FlightCache.destination.in_(destinations),
                            FlightCache.travel_date.in_(dates)
                        )
                    )
                )
                relevant_cache = cache_res.scalars().all()
            else:
                relevant_cache = []

            # Build lookup dict for O(1) access
            cache_lookup = {}
            for cache in relevant_cache:
                key = (cache.origin, cache.destination, cache.travel_date)
                cache_lookup[key] = cache.created_at

            # Now check staleness in memory
            for combo in route_date_combos:
                key = (combo["origin"], combo["destination"], combo["date"])
                last_updated = cache_lookup.get(key)

                is_stale = False
                if not last_updated:
                    is_stale = True
                else:
                    if last_updated.tzinfo is None:
                        last_updated = pytz.UTC.localize(last_updated)
                    age = datetime.now(pytz.UTC) - last_updated
                    if age > timedelta(minutes=stale_mins):
                        is_stale = True

                    last_updated_local = last_updated.astimezone(combo["tz"])
                    if last_updated_local.date() < combo["now_local"].date():
                        is_stale = True

                if is_stale:
                    tasks.append({"origin": combo["origin"], "destination": combo["destination"], "date": combo["date"]})
                else:
                    stats["skipped"] += 1
        
        # Step 2: Process Queue (No active session)
        if tasks:
            logger.info(f"AutoScraper: Found {len(tasks)} stale tasks.")
            update_job(self.job_id, message=f"Processing {len(tasks)} stale tasks...")
            
            # Create fresh engine without session
            engine = AsyncScraperEngine(session=None)
            res = await engine.process_queue(tasks, mode="auto")
            
            stats["scraped"] = res["scraped"]
            stats["errors"] = res["errors"]

        return stats

class MidnightScraper(BaseJob):
    def __init__(self):
        super().__init__()
        self.job_name = "MidnightScraper"

    async def _execute_job(self, stop_check=None):
        logger.info("Starting MidnightScraper Job...")
        stats = {"scraped": 0, "skipped": 0, "errors": 0, "total": 0}
        
        tasks = []
        target_airports_count = 0

        # Step 1: Gather Data
        async with SessionLocal() as session:
            # Get configurable settings
            check_duration_mins = int(await self.get_setting(session, "midnight_check_duration", "30"))

            res = await session.execute(select(Airport))
            all_airports = res.scalars().all()

            target_airports = []
            for ap in all_airports:
                try:
                    tz = pytz.timezone(ap.timezone or "UTC")
                    now = datetime.now(tz)
                    # Check if we're within the midnight window
                    if now.hour == 0 and now.minute < check_duration_mins:
                        target_airports.append(ap.code)
                except: pass

            if not target_airports:
                logger.info("MidnightScraper: No airports in midnight window")
                return stats
            
            target_airports_count = len(target_airports)

            res = await session.execute(select(RoutePair).where(
                and_(RoutePair.is_active == True, RoutePair.origin.in_(target_airports))
            ))
            routes = res.scalars().all()

            # Build timezone map and cache datetime per timezone
            airport_tz_map = {a.code: pytz.timezone(a.timezone or "UTC") for a in all_airports if a.code in target_airports}
            tz_date_cache = {}  # Cache today_str per timezone

            for tz in set(airport_tz_map.values()):
                tz_date_cache[tz] = datetime.now(tz).strftime("%Y-%m-%d")

            for route in routes:
                tz = airport_tz_map.get(route.origin)
                if not tz: continue
                today_str = tz_date_cache[tz]

                tasks.append({"origin": route.origin, "destination": route.destination, "date": today_str})
                stats["total"] += 1

        # Step 2: Process Queue
        if tasks:
            logger.info(f"MidnightScraper: Forcing refresh for {len(tasks)} routes across {target_airports_count} airports.")
            update_job(self.job_id, message=f"Refreshing {len(tasks)} routes...")
            
            engine = AsyncScraperEngine(session=None)
            res = await engine.process_queue(tasks, mode="midnight")
            
            stats["scraped"] = res["scraped"]
            stats["errors"] = res["errors"]

        return stats

class ThreeWeekScraper(BaseJob):
    def __init__(self):
        super().__init__()
        self.job_name = "3WeekScraper"
        self.current_run_id = None

    async def save_progress(self, completed_routes_set):
        """Save progress to database for resume capability"""
        if not self.current_run_id:
            return

        async with SessionLocal() as session:
            stmt = select(ScraperRun).where(ScraperRun.id == self.current_run_id)
            result = await session.execute(stmt)
            run = result.scalar_one_or_none()

            if run:
                # Convert set to list for JSON serialization
                completed_list = [list(r) for r in completed_routes_set]
                run.details = {
                    "completed_routes": completed_list,
                    "total_completed": len(completed_list),
                    "last_updated": datetime.now(pytz.UTC).isoformat(),
                    "status": "in_progress"  # Mark as actively being worked on
                }
                # Update the completed_at to track when we last made progress
                # This acts as a heartbeat to detect timeouts
                run.completed_at = datetime.now(pytz.UTC)
                await session.commit()
                logger.info(f"Progress saved: {len(completed_list)} routes completed")

    async def _execute_job(self, stop_check=None):
        logger.info("Starting ThreeWeekScraper Job...")
        start_time = datetime.now(pytz.UTC)
        stats = {"scraped": 0, "skipped": 0, "errors": 0, "total": 0}

        scrape_tasks = []
        cached_snapshots = []
        completed_routes = set()  # Track completed routes for resume logic

        # Step 0: Check for incomplete run from today and resume if needed
        async with SessionLocal() as session:
            today_utc = datetime.now(pytz.UTC).date()

            # Look for running or incomplete jobs from today
            stmt = select(ScraperRun).where(
                and_(
                    ScraperRun.job_type == "3WeekScraper",
                    func.date(ScraperRun.started_at) == today_utc,
                    ScraperRun.status == "running"
                )
            ).order_by(ScraperRun.started_at.desc())

            result = await session.execute(stmt)
            existing_run = result.scalars().first()

            if existing_run:
                logger.info(f"Found incomplete 3WeekScraper run #{existing_run.id} from today. Resuming...")
                self.current_run_id = existing_run.id

                # Load completed routes from previous attempt
                if existing_run.details and "completed_routes" in existing_run.details:
                    completed_routes = set(tuple(r) for r in existing_run.details["completed_routes"])
                    logger.info(f"Resuming: {len(completed_routes)} routes already completed")
                    stats["skipped"] = len(completed_routes)

        # Step 1: Gather Data & Check Cache
        async with SessionLocal() as session:
            # Load stale minutes (default to 60 mins for "fresh enough")
            stale_mins = int(await self.get_setting(session, "scraper_stale_minutes", "60"))
            
            res = await session.execute(select(RoutePair).where(RoutePair.is_active == True))
            routes = res.scalars().all()

            today = datetime.utcnow().date()
            dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(21)]

            # --- Duplicate Snapshot Prevention ---
            # 1. Fetch snapshots that were already created today.
            today_utc = datetime.now(pytz.UTC).date()
            stmt = select(FareSnapshot.origin, FareSnapshot.destination, FareSnapshot.travel_date).where(
                func.date(FareSnapshot.scraped_at) == today_utc
            )
            result = await session.execute(stmt)
            existing_snapshots_today = set(result.all()) # set of (origin, dest, date) tuples
            
            # Generate all potential tasks
            all_potential_tasks = []
            for route in routes:
                for date_str in dates:
                    all_potential_tasks.append({
                        "origin": route.origin,
                        "destination": route.destination,
                        "date": date_str
                    })
            
            stats["total"] = len(all_potential_tasks)

            # Optimization: Fetch cache for ALL potential tasks
            origins = {t["origin"] for t in all_potential_tasks}
            destinations = {t["destination"] for t in all_potential_tasks}
            date_set = set(dates)

            if origins and destinations and date_set:
                cache_res = await session.execute(
                    select(FlightCache).where(
                        and_(
                            FlightCache.origin.in_(origins),
                            FlightCache.destination.in_(destinations),
                            FlightCache.travel_date.in_(date_set)
                        )
                    )
                )
                relevant_cache = cache_res.scalars().all()
            else:
                relevant_cache = []

            # Map cache by key
            cache_lookup = {}
            for c in relevant_cache:
                cache_lookup[(c.origin, c.destination, c.travel_date)] = c

            # Classify Tasks
            for task in all_potential_tasks:
                key = (task["origin"], task["destination"], task["date"])

                # Skip if already completed in a previous attempt today (resume logic)
                if key in completed_routes:
                    stats["skipped"] += 1
                    continue

                # 2. Skip if we already snapshotted this route for its travel_date today
                if key in existing_snapshots_today:
                    stats["skipped"] += 1
                    continue

                cache_entry = cache_lookup.get(key)
                
                is_fresh = False
                if cache_entry:
                    if cache_entry.created_at.tzinfo is None:
                        created_at = pytz.UTC.localize(cache_entry.created_at)
                    else:
                        created_at = cache_entry.created_at
                        
                    age = datetime.now(pytz.UTC) - created_at
                    if age < timedelta(minutes=stale_mins):
                        is_fresh = True
                
                if is_fresh:
                    # Create Snapshot from Cache
                    try:
                        flights = decompress_data(cache_entry.data)
                        metrics = AsyncScraperEngine.calculate_metrics(flights)
                        
                        cached_snapshots.append(FareSnapshot(
                            origin=task["origin"], destination=task["destination"], travel_date=task["date"],
                            min_price_standard=metrics["min_price_standard"],
                            seats_standard=metrics["seats_standard"],
                            min_price_den=metrics["min_price_den"],
                            seats_den=metrics["seats_den"],
                            min_price_gowild=metrics["min_price_gowild"],
                            seats_gowild=metrics["seats_gowild"],
                            data=cache_entry.data # Reuse compressed blob
                        ))
                        stats["skipped"] += 1
                        # 3. Add key to set to prevent re-adding in this same run
                        existing_snapshots_today.add(key)
                    except Exception as e:
                        logger.error(f"Error processing cached snapshot for {key}: {e}")
                        scrape_tasks.append(task) # Fallback to scrape
                else:
                    scrape_tasks.append(task)

            # Save Cached Snapshots Immediately
            if cached_snapshots:
                logger.info(f"ThreeWeekScraper: Found {len(cached_snapshots)} fresh cached items. Creating snapshots directly.")

                # Bulk insert in chunks to avoid massive query size
                CHUNK_SIZE = 500
                for i in range(0, len(cached_snapshots), CHUNK_SIZE):
                    chunk = cached_snapshots[i:i + CHUNK_SIZE]
                    session.add_all(chunk)
                    await session.commit()

                    # Mark these routes as completed for resume tracking
                    for snap in chunk:
                        completed_routes.add((snap.origin, snap.destination, snap.travel_date))

                update_job(self.job_id, message=f"Created {len(cached_snapshots)} snapshots from cache...")

                # Save progress after processing cached snapshots
                await self.save_progress(completed_routes)

        # Step 2: Scrape Remaining Tasks in Batches
        if scrape_tasks:
            logger.info(f"ThreeWeekScraper: Queuing {len(scrape_tasks)} stale/missing tasks for scraping.")
            update_job(self.job_id, message=f"Scraping {len(scrape_tasks)} routes...")

            # Process in smaller batches to allow progress saving
            BATCH_SIZE = 10  # Reduced from 50 to 10 for more frequent updates
            total_scraped = 0
            total_errors = 0

            engine = AsyncScraperEngine(session=None)

            for i in range(0, len(scrape_tasks), BATCH_SIZE):
                # Check for global timeout (e.g. 5 min limit on serverless)
                # We stop at 4.5 mins (270s) to allow safe exit
                elapsed = (datetime.now(pytz.UTC) - start_time).total_seconds()
                if elapsed > 270:
                    logger.info(f"Time limit reached ({elapsed:.1f}s). Pausing job to resume later...")
                    update_job(self.job_id, message=f"Pausing (Time Limit). Scraped: {total_scraped}")
                    self.final_status = "running" # Keep status as 'running' so it resumes next time
                    await self.save_progress(completed_routes)
                    break

                batch = scrape_tasks[i:i + BATCH_SIZE]
                batch_num = (i // BATCH_SIZE) + 1
                total_batches = (len(scrape_tasks) + BATCH_SIZE - 1) // BATCH_SIZE

                logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} routes)")
                update_job(self.job_id, message=f"Batch {batch_num}/{total_batches}: Scraping {len(batch)} routes...")

                try:
                    res = await engine.process_queue(batch, mode="3week")
                    total_scraped += res.get("scraped", 0)
                    total_errors += res.get("errors", 0)

                    # Mark this batch as completed
                    for task in batch:
                        key = (task["origin"], task["destination"], task["date"])
                        completed_routes.add(key)

                    # Save progress after each batch
                    await self.save_progress(completed_routes)

                    logger.info(f"Batch {batch_num}/{total_batches} complete. Scraped: {res.get('scraped', 0)}, Errors: {res.get('errors', 0)}")

                except Exception as e:
                    logger.error(f"Error processing batch {batch_num}: {e}")
                    total_errors += len(batch)
                    # Still save progress even if batch failed
                    await self.save_progress(completed_routes)

                # Check if we should stop
                if stop_check and stop_check():
                    logger.info("Stop signal received, saving progress and exiting")
                    await self.save_progress(completed_routes)
                    break

            stats["scraped"] = total_scraped
            stats["errors"] = total_errors

        return stats