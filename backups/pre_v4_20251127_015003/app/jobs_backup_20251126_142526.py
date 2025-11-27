import asyncio
import logging
import traceback
import uuid
from datetime import datetime, timedelta
import pytz
from sqlalchemy import select, func, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models import RoutePair, Airport, FlightCache, SystemSetting, ScraperRun
from app.scraper import AsyncScraperEngine
from app.job_manager import JOBS, check_stop, update_job, complete_job, register_job

logger = logging.getLogger(__name__)

class BaseJob:
    def __init__(self):
        self.engine = None
        self.job_name = "Unknown"
        self.heartbeat_id = None
        self.mode = "manual"
        self.job_id = None

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

        async with SessionLocal() as session:
            # Create 'Running' log
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
        
        async with SessionLocal() as session:
            engine = await self.get_engine(session)
            stale_mins = int(await self.get_setting(session, "scraper_stale_minutes", "60"))
            
            res = await session.execute(select(RoutePair).where(RoutePair.is_active == True))
            routes = res.scalars().all()
            
            airports_res = await session.execute(select(Airport))
            airports = {a.code: a for a in airports_res.scalars().all()}
            
            tasks = []
            
            for route in routes:
                if stop_check and stop_check(): break

                origin_ap = airports.get(route.origin)
                if not origin_ap: continue
                
                tz = pytz.timezone(origin_ap.timezone or "UTC")
                now_local = datetime.now(tz)
                dates_to_check = [
                    now_local.strftime("%Y-%m-%d"),
                    (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
                ]
                
                for date_str in dates_to_check:
                    stats["total"] += 1
                    
                    stmt = select(FlightCache.created_at).where(
                        and_(FlightCache.origin == route.origin, 
                             FlightCache.destination == route.destination,
                             FlightCache.travel_date == date_str)
                    )
                    cache_res = await session.execute(stmt)
                    last_updated = cache_res.scalar_one_or_none()
                    
                    is_stale = False
                    if not last_updated:
                        is_stale = True
                    else:
                        if last_updated.tzinfo is None: last_updated = pytz.UTC.localize(last_updated)
                        age = datetime.now(pytz.UTC) - last_updated
                        if age > timedelta(minutes=stale_mins):
                            is_stale = True
                        
                        last_updated_local = last_updated.astimezone(tz)
                        if last_updated_local.date() < now_local.date():
                            is_stale = True

                    if is_stale:
                        tasks.append({"origin": route.origin, "destination": route.destination, "date": date_str})
                    else:
                        stats["skipped"] += 1

            if tasks and not (stop_check and stop_check()):
                logger.info(f"AutoScraper: Found {len(tasks)} stale tasks.")
                update_job(self.job_id, message=f"Processing {len(tasks)} stale tasks...")
                res = await engine.process_queue(tasks, mode="auto", stop_check=stop_check)
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

        async with SessionLocal() as session:
            engine = await self.get_engine(session)

            # Get configurable settings
            check_duration_mins = int(await self.get_setting(session, "midnight_check_duration", "30"))
            check_interval_mins = int(await self.get_setting(session, "midnight_check_interval", "2"))

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

            res = await session.execute(select(RoutePair).where(
                and_(RoutePair.is_active == True, RoutePair.origin.in_(target_airports))
            ))
            routes = res.scalars().all()

            airport_tz_map = {a.code: pytz.timezone(a.timezone or "UTC") for a in all_airports if a.code in target_airports}

            tasks = []
            for route in routes:
                if stop_check and stop_check(): break
                tz = airport_tz_map.get(route.origin)
                if not tz: continue
                today_str = datetime.now(tz).strftime("%Y-%m-%d")

                tasks.append({"origin": route.origin, "destination": route.destination, "date": today_str})
                stats["total"] += 1

            if tasks and not (stop_check and stop_check()):
                logger.info(f"MidnightScraper: Forcing refresh for {len(tasks)} routes across {len(target_airports)} airports.")
                update_job(self.job_id, message=f"Refreshing {len(tasks)} routes...")
                res = await engine.process_queue(tasks, mode="midnight", stop_check=stop_check)
                stats["scraped"] = res["scraped"]
                stats["errors"] = res["errors"]

            return stats

class ThreeWeekScraper(BaseJob):
    def __init__(self):
        super().__init__()
        self.job_name = "3WeekScraper"

    async def _execute_job(self, stop_check=None):
        logger.info("Starting ThreeWeekScraper Job...")
        stats = {"scraped": 0, "skipped": 0, "errors": 0, "total": 0}
        
        async with SessionLocal() as session:
            engine = await self.get_engine(session)
            res = await session.execute(select(RoutePair).where(RoutePair.is_active == True))
            routes = res.scalars().all()
            
            tasks = []
            today = datetime.utcnow().date()
            
            for route in routes:
                if stop_check and stop_check(): break
                for i in range(21):
                    date_obj = today + timedelta(days=i)
                    tasks.append({
                        "origin": route.origin,
                        "destination": route.destination,
                        "date": date_obj.strftime("%Y-%m-%d")
                    })
                    stats["total"] += 1
            
            if tasks and not (stop_check and stop_check()):
                logger.info(f"ThreeWeekScraper: Queuing {len(tasks)} tasks.")
                update_job(self.job_id, message=f"Queued {len(tasks)} tasks...")
                res = await engine.process_queue(tasks, mode="3week", stop_check=stop_check)
                stats["scraped"] = res["scraped"]
                stats["errors"] = res["errors"]
                
            return stats