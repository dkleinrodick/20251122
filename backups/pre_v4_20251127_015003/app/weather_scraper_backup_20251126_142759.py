import asyncio
import logging
import traceback
from datetime import datetime, timedelta
import httpx
import pytz
import random
import uuid
from sqlalchemy import select, delete
from app.database import SessionLocal
from app.models import Airport, WeatherData, ScraperRun
from app.job_manager import register_job, update_job, complete_job, check_stop, JOBS

logger = logging.getLogger(__name__)

class WeatherScraper:
    def __init__(self):
        self.job_name = "WeatherScraper"
        self.heartbeat_id = None
        self.mode = "manual"
        self.job_id = None

    async def run(self, heartbeat_id=None, mode=None, job_id=None):
        """Wrapper to handle database logging for the job."""
        if heartbeat_id is not None:
            self.heartbeat_id = heartbeat_id
        if mode is not None:
            self.mode = mode
            
        self.job_id = job_id or str(uuid.uuid4())
        
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

        stats = {"airports_updated": 0, "errors": 0, "details": []}
        status = "completed"
        error_msg = None
        
        stop_check = lambda: check_stop(self.job_id)

        try:
            if stop_check():
                status = "cancelled"
                update_job(self.job_id, status="cancelled", message="Cancelled before start")
            else:
                logger.info("Starting WeatherScraper Job...")
                await self.prune_old_data()
                
                if stop_check():
                    status = "cancelled"
                    update_job(self.job_id, status="cancelled", message="Cancelled")
                else:
                    airports_count = await self.update_forecasts(stop_check)
                    stats["airports_updated"] = airports_count
                    complete_job(self.job_id, message=f"Weather updated for {airports_count} airports.")

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
                run_log.total_routes = stats.get("airports_updated", 0)
                run_log.details = stats

                await session.commit()

    async def prune_old_data(self):
        logger.info("Pruning old weather data...")
        update_job(self.job_id, message="Pruning old data...")
        async with SessionLocal() as session:
            tz = pytz.timezone('America/Los_Angeles')
            today_pst = datetime.now(tz).date()
            today_str = today_pst.strftime("%Y-%m-%d")
            
            stmt = delete(WeatherData).where(WeatherData.date < today_str)
            res = await session.execute(stmt)
            logger.info(f"Deleted {res.rowcount} past weather records.")
            
            res_airports = await session.execute(select(Airport.code))
            valid_codes = [r for r in res_airports.scalars().all()]
            
            if valid_codes:
                stmt_orphans = delete(WeatherData).where(WeatherData.airport_code.not_in(valid_codes))
                res_orphans = await session.execute(stmt_orphans)
                logger.info(f"Deleted {res_orphans.rowcount} orphaned weather records.")
            
            await session.commit()

    async def update_forecasts(self, stop_check=None):
        logger.info("Updating forecasts...")
        update_job(self.job_id, message="Fetching forecasts...")

        async with SessionLocal() as session:
            stmt = select(Airport).where(Airport.latitude != None, Airport.longitude != None)
            airports_objs = (await session.execute(stmt)).scalars().all()

            # Detach data for concurrency
            airports_data = [
                {"code": a.code, "latitude": a.latitude, "longitude": a.longitude}
                for a in airports_objs
            ]

        total = len(airports_data)
        logger.info(f"Fetching weather for {total} airports...")
        airports_updated = 0

        # Reduce batch size to avoid 429s
        chunk_size = 10
        processed = 0
        
        for i in range(0, total, chunk_size):
            if stop_check and stop_check(): break
            
            chunk = airports_data[i:i + chunk_size]
            
            pct = int((processed / total) * 100)
            update_job(self.job_id, progress=pct, message=f"Updating weather {processed}/{total}...")

            tasks = [self._fetch_airport_weather(ap) for ap in chunk]
            results = await asyncio.gather(*tasks)

            # Save results in a fresh session transaction
            async with SessionLocal() as session:
                for res_list in results:
                    if not res_list: continue
                    airports_updated += 1
                    for w_data in res_list:
                        # Merge logic
                        stmt = select(WeatherData).where(
                            WeatherData.airport_code == w_data['code'],
                            WeatherData.date == w_data['date']
                        )
                        existing = (await session.execute(stmt)).scalar_one_or_none()

                        if existing:
                            existing.temp_high = w_data['temp']
                            existing.condition_code = w_data['condition']
                            existing.updated_at = datetime.now(pytz.UTC)
                        else:
                            session.add(WeatherData(
                                airport_code=w_data['code'],
                                date=w_data['date'],
                                temp_high=w_data['temp'],
                                condition_code=w_data['condition'],
                                updated_at=datetime.now(pytz.UTC)
                            ))

                await session.commit()

            processed += len(chunk)
            logger.info(f"Processed batch {i+1}-{min(i+chunk_size, total)}")
            await asyncio.sleep(2.0) # Increased delay between batches

        return airports_updated

    async def _fetch_airport_weather(self, airport_dict) -> list:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": airport_dict["latitude"],
            "longitude": airport_dict["longitude"],
            "daily": "weathercode,temperature_2m_max",
            "timezone": "auto",
            "forecast_days": 16 
        }
        
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                # Small jitter to prevent thundering herd within the batch
                await asyncio.sleep(random.uniform(0.1, 0.5))
                
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(url, params=params)
                    
                    if resp.status_code == 429:
                        logger.warning(f"Rate limited (429) for {airport_dict['code']}. Retrying in 5s...")
                        await asyncio.sleep(5) # Specific backoff for rate limit
                        continue
                        
                    if resp.status_code != 200:
                        logger.warning(f"Weather failed for {airport_dict['code']}: {resp.status_code}")
                        return []
                    
                    data = resp.json()
                    daily = data.get("daily", {})
                    times = daily.get("time", [])
                    codes = daily.get("weathercode", [])
                    temps = daily.get("temperature_2m_max", [])
                    
                    results = []
                    for k, date_str in enumerate(times):
                        results.append({
                            "code": airport_dict['code'],
                            "date": date_str,
                            "condition": codes[k] if k < len(codes) else 0,
                            "temp": temps[k] if k < len(temps) else 0.0
                        })
                    return results
                    
            except Exception as e:
                logger.error(f"Weather error {airport_dict['code']} (Attempt {attempt+1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                else:
                    return []
        return []