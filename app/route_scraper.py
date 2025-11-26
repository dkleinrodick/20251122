import asyncio
import logging
import traceback
import uuid
from datetime import datetime, timedelta
import httpx
import re
import json
import pytz
from sqlalchemy import select, or_, and_, update, insert
from app.database import SessionLocal
from app.models import RoutePair, Airport, SystemSetting, ScraperRun
from app.airports_data import AIRPORTS_LIST, AIRPORT_MAPPING
from app.scraper import FrontierClient, ProxyManager
from app.job_manager import register_job, update_job, complete_job, check_stop, JOBS

logger = logging.getLogger(__name__)

class RouteScraper:
    SITEMAP_URL = "https://flights.flyfrontier.com/en/sitemap/city-to-city-flights/page-1"

    def __init__(self):
        self.job_name = "RouteScraper"
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

        stats = {"discovered": 0, "validated": 0, "errors": 0}
        status = "completed"
        error_msg = None
        
        stop_check = lambda: check_stop(self.job_id)

        try:
            if stop_check():
                status = "cancelled"
                update_job(self.job_id, status="cancelled", message="Cancelled before start")
            else:
                logger.info("Starting RouteScraper Job...")
                
                # Step 0: Sync Airports
                await self.sync_airports()
                
                discovered = await self.discover_routes(stop_check)
                
                if stop_check():
                    status = "cancelled"
                    update_job(self.job_id, status="cancelled", message="Cancelled during discovery")
                else:
                    validated = await self.validate_routes(stop_check)
                    stats["discovered"] = discovered
                    stats["validated"] = validated
                    
                    if stop_check():
                        status = "cancelled"
                        update_job(self.job_id, status="cancelled", message="Cancelled during validation")
                    else:
                        complete_job(self.job_id, message=f"Completed. Discovered: {discovered}, Validated: {validated}")

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
                run_log.total_routes = stats.get("discovered", 0) + stats.get("validated", 0)
                run_log.routes_scraped = stats.get("validated", 0)
                run_log.routes_failed = stats.get("errors", 0)
                run_log.details = stats

                await session.commit()

    async def sync_airports(self):
        logger.info("Step 0: Syncing Airports...")
        update_job(self.job_id, message="Syncing Airports...")
        try:
            async with SessionLocal() as session:
                # Fetch all existing airports into a dict for comparison
                res = await session.execute(select(Airport))
                existing_map = {a.code: a for a in res.scalars().all()}
                
                to_add = []
                updates = 0
                
                for a_data in AIRPORTS_LIST:
                    code = a_data["code"]
                    if code in existing_map:
                        # Update existing if changed
                        obj = existing_map[code]
                        changed = False
                        if obj.city_name != a_data["city"]:
                            obj.city_name = a_data["city"]
                            changed = True
                        if obj.timezone != a_data.get("timezone", "UTC"):
                            obj.timezone = a_data.get("timezone", "UTC")
                            changed = True
                        if obj.latitude != a_data.get("lat"):
                            obj.latitude = a_data.get("lat")
                            changed = True
                        if obj.longitude != a_data.get("lon"):
                            obj.longitude = a_data.get("lon")
                            changed = True
                        
                        if changed:
                            updates += 1
                    else:
                        # Insert new
                        to_add.append({
                             "code": code, 
                             "city_name": a_data["city"],
                             "timezone": a_data.get("timezone", "UTC"),
                             "latitude": a_data.get("lat"),
                             "longitude": a_data.get("lon")
                         })
                
                if to_add:
                    await session.execute(insert(Airport).values(to_add))
                    
                if to_add or updates > 0:
                    await session.commit()
                    logger.info(f"Route Scraper: Seeded {len(to_add)} new, Updated {updates} existing airports.")
                else:
                    logger.info("Route Scraper: Airports up to date.")
                    
        except Exception as e:
            logger.error(f"Airport sync failed: {e}")
            # Continue anyway

    async def discover_routes(self, stop_check=None):
        logger.info("Step 1: Discovering Routes from Sitemap...")
        update_job(self.job_id, message="Step 1: Discovering Routes...")
        added = 0
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                if stop_check and stop_check(): return 0
                resp = await client.get(self.SITEMAP_URL)
                if resp.status_code != 200:
                    logger.error(f"Failed to fetch sitemap: {resp.status_code}")
                    return 0

                content = resp.text
                start_marker = '<script id="__NEXT_DATA__" type="application/json">'
                end_marker = '</script>'
                start_idx = content.find(start_marker)
                if start_idx == -1:
                    logger.error("Could not find NEXT_DATA in sitemap")
                    return 0
                
                start_idx += len(start_marker)
                end_idx = content.find(end_marker, start_idx)
                if end_idx == -1:
                    logger.error("Could not find end of NEXT_DATA")
                    return 0
                
                json_str = content[start_idx:end_idx]
                data = json.loads(json_str)
                
                try:
                    links = data['props']['pageProps']['sitemap']['links']
                except KeyError:
                    logger.error("Invalid sitemap JSON structure")
                    return 0

                potential_routes = set()
                
                for link in links:
                    if stop_check and stop_check(): return added
                    route_name = link.get('name', '')
                    parts = []
                    if " to " in route_name:
                        clean = route_name.replace("Flights from ", "")
                        parts = clean.split(" to ")
                    elif " - " in route_name:
                        parts = route_name.split(" - ")
                        
                    if len(parts) == 2:
                        origin_name = parts[0].strip()
                        dest_name = parts[1].strip()
                        
                        origins = self._resolve_city(origin_name)
                        dests = self._resolve_city(dest_name)
                        
                        for o in origins:
                            for d in dests:
                                if o != d:
                                    potential_routes.add((o, d))

                logger.info(f"Found {len(potential_routes)} potential routes from sitemap.")
                
                async with SessionLocal() as session:
                    existing_res = await session.execute(select(RoutePair))
                    existing = {(r.origin, r.destination): r for r in existing_res.scalars().all()}

                    for origin, dest in potential_routes:
                        if (origin, dest) not in existing:
                            logger.info(f"New potential route found: {origin}-{dest}")
                            session.add(RoutePair(origin=origin, destination=dest, is_active=False, error_count=0))
                            added += 1

                    await session.commit()
                    logger.info(f"Added {added} new routes to DB (Pending Validation).")

        except Exception as e:
            logger.error(f"Discovery failed: {e}")

        return added

    def _resolve_city(self, city_name: str) -> list:
        if city_name in AIRPORT_MAPPING:
            return AIRPORT_MAPPING[city_name]
        if "," in city_name:
            simple_city = city_name.split(",")[0].strip()
            if simple_city in AIRPORT_MAPPING:
                return AIRPORT_MAPPING[simple_city]
        return []

    async def validate_routes(self, stop_check=None):
        logger.info("Step 2: Validating Pending/Stale Routes...")
        update_job(self.job_id, message="Step 2: Validating Routes...")
        validated_count = 0

        async with SessionLocal() as session:
            cutoff = datetime.now(pytz.UTC) - timedelta(days=3)
            
            stmt = select(RoutePair).where(
                or_(
                    RoutePair.is_active == False,
                    RoutePair.last_validated == None,
                    RoutePair.last_validated < cutoff
                )
            )
            
            routes_objs = (await session.execute(stmt)).scalars().all()
            
            if not routes_objs:
                logger.info("No routes need validation.")
                return 0

            total = len(routes_objs)
            logger.info(f"Validating {total} routes...")
            update_job(self.job_id, message=f"Validating {total} routes...")
            
            routes_data = [
                {"id": r.id, "origin": r.origin, "destination": r.destination}
                for r in routes_objs
            ]
            
            proxy_mgr = ProxyManager(session)
            await proxy_mgr.load_proxies()
            
            # Fetch concurrency setting
            setting = await session.execute(select(SystemSetting).where(SystemSetting.key == "scraper_worker_count"))
            setting = setting.scalar_one_or_none()
            max_concurrent = int(setting.value) if setting else 20
            
            sem = asyncio.Semaphore(max_concurrent)
            
            # Prepare logic for multi-day check
            intl_codes = {a['code'] for a in AIRPORTS_LIST if a.get('is_international')}
            today_utc = datetime.utcnow()

            async def check_route(route_dict):
                if stop_check and stop_check(): return {"id": route_dict["id"], "status": "skip"}
                async with sem:
                    proxy = await proxy_mgr.get_next_proxy()
                    client = FrontierClient(proxy=proxy, timeout=20.0)
                    route_id = route_dict["id"]
                    origin = route_dict["origin"]
                    dest = route_dict["destination"]
                    
                    result = {"id": route_id, "status": "invalid"} # Default to invalid until proven valid
                    
                    days_to_check = 11 if (origin in intl_codes or destination in intl_codes) else 3
                    
                    try:
                        await client.authenticate()
                        
                        for i in range(days_to_check):
                            if stop_check and stop_check(): 
                                result["status"] = "skip"
                                break
                                
                            check_date = (today_utc + timedelta(days=i)).strftime("%Y-%m-%d")
                            
                            try:
                                await client.search(origin, dest, check_date)
                                # If we get here (200 OK), the route is valid in the system
                                result["status"] = "valid"
                                logger.info(f"[VALID] {origin}-{dest}")
                                break # Found a valid day, stop checking
                            except httpx.HTTPStatusError as http_err:
                                if http_err.response.status_code == 400:
                                    # 400 usually means route invalid OR date invalid for route.
                                    # We keep trying other days.
                                    pass
                                else:
                                    # 503/403 etc - treat as skip or keep trying?
                                    logger.warning(f"[WARN] {origin}-{dest} ({http_err.response.status_code})")
                            except Exception:
                                # Network error, proxy error, etc.
                                pass
                        
                        if result["status"] == "invalid":
                            logger.warning(f"[INVALID] {origin}-{dest} (Checked {days_to_check} days)")
                            
                    except Exception as e:
                        logger.error(f"[ERROR] {origin}-{dest}: {e}")
                        result["status"] = "skip" # Don't mark invalid on auth/net error
                    finally:
                        await client.close()
                    
                    return result

            # Process in batches
            batch_size = 200 
            for i in range(0, total, batch_size):
                if stop_check and stop_check(): break
                
                batch = routes_data[i:i + batch_size]
                logger.info(f"Processing validation batch {i+1}-{min(i+batch_size, total)} of {total}")
                update_job(self.job_id, progress=int((i/total)*100), message=f"Validating {i+1}/{total}")
                
                tasks = [check_route(r) for r in batch]
                results = await asyncio.gather(*tasks)
                
                valid_ids = [r["id"] for r in results if r["status"] == "valid"]
                invalid_ids = [r["id"] for r in results if r["status"] == "invalid"]
                
                if valid_ids:
                    await session.execute(
                        update(RoutePair)
                        .where(RoutePair.id.in_(valid_ids))
                        .values(is_active=True, last_validated=datetime.now(pytz.UTC), error_count=0)
                    )
                    validated_count += len(valid_ids)
                
                if invalid_ids:
                    await session.execute(
                        update(RoutePair)
                        .where(RoutePair.id.in_(invalid_ids))
                        .values(is_active=False, last_validated=datetime.now(pytz.UTC))
                    )

                await session.commit()

            logger.info("Validation complete.")
            return validated_count
