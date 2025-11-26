import asyncio
import logging
from datetime import datetime, timedelta
import pytz
from sqlalchemy import select
from app.database import SessionLocal
from app.models import SystemSetting
from app.jobs import AutoScraper, MidnightScraper, ThreeWeekScraper
from app.route_scraper import RouteScraper
from app.weather_scraper import WeatherScraper

logger = logging.getLogger(__name__)

class SchedulerLogic:
    async def run_heartbeat(self, background_tasks):
        logger.info("Heartbeat: Checking schedules...")
        async with SessionLocal() as session:
            await self._check_auto(session, background_tasks)
            await self._check_midnight(session, background_tasks)
            await self._check_daily(session, background_tasks, "weather", WeatherScraper)
            await self._check_daily(session, background_tasks, "3week", ThreeWeekScraper)
            await self._check_daily(session, background_tasks, "route_sync", RouteScraper)

    async def _get_setting(self, session, key, default):
        res = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
        s = res.scalar_one_or_none()
        return s.value if s else default

    async def _set_setting(self, session, key, value):
        res = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
        s = res.scalar_one_or_none()
        if s:
            s.value = str(value)
        else:
            session.add(SystemSetting(key=key, value=str(value)))
        await session.commit()

    async def _check_auto(self, session, background_tasks):
        enabled = await self._get_setting(session, "schedule_auto_enabled", "true")
        if enabled.lower() != "true": return

        interval = int(await self._get_setting(session, "schedule_auto_interval", "30"))
        last_run_str = await self._get_setting(session, "last_run_auto", "")
        
        should_run = False
        if not last_run_str:
            should_run = True
        else:
            last_run = datetime.fromisoformat(last_run_str)
            if datetime.utcnow() - last_run > timedelta(minutes=interval):
                should_run = True
        
        if should_run:
            logger.info("Heartbeat: Triggering AutoScraper")
            job = AutoScraper()
            background_tasks.add_task(job.run)
            await self._set_setting(session, "last_run_auto", datetime.utcnow().isoformat())

    async def _check_midnight(self, session, background_tasks):
        # Midnight runs frequently (every 15 mins) to scan timezones
        enabled = await self._get_setting(session, "schedule_midnight_enabled", "true")
        if enabled.lower() != "true": return

        interval = int(await self._get_setting(session, "schedule_midnight_interval", "15"))
        last_run_str = await self._get_setting(session, "last_run_midnight", "")
        
        should_run = False
        if not last_run_str:
            should_run = True
        else:
            last_run = datetime.fromisoformat(last_run_str)
            if datetime.utcnow() - last_run > timedelta(minutes=interval):
                should_run = True
        
        if should_run:
            logger.info("Heartbeat: Triggering MidnightScraper")
            job = MidnightScraper()
            background_tasks.add_task(job.run)
            await self._set_setting(session, "last_run_midnight", datetime.utcnow().isoformat())

    async def _check_daily(self, session, background_tasks, job_key, job_class):
        enabled = await self._get_setting(session, f"schedule_{job_key}_enabled", "true")
        if enabled.lower() != "true": return

        # Default times (UTC)
        defaults = {
            "weather": "09:30", # 3:30 AM CST
            "3week": "09:00",
            "route_sync": "00:00"
        }
        
        target_time_str = await self._get_setting(session, f"schedule_{job_key}_time", defaults.get(job_key, "00:00"))
        last_run_str = await self._get_setting(session, f"last_run_{job_key}", "")
        
        now = datetime.utcnow()
        
        # Check if run today already
        if last_run_str:
            last_run = datetime.fromisoformat(last_run_str)
            if last_run.date() == now.date():
                return # Already ran today

        # Check if time is reached
        try:
            target_h, target_m = map(int, target_time_str.split(":"))
            if now.hour > target_h or (now.hour == target_h and now.minute >= target_m):
                logger.info(f"Heartbeat: Triggering {job_key}")
                job = job_class()
                background_tasks.add_task(job.run)
                await self._set_setting(session, f"last_run_{job_key}", now.isoformat())
        except Exception as e:
            logger.error(f"Scheduler error for {job_key}: {e}")
