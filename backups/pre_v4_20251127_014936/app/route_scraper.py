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
                    # Fetch only existing route tuples (origin, dest) instead of full objects
                    existing_res = await session.execute(
                        select(RoutePair.origin, RoutePair.destination)
                    )
                    existing = {(r.origin, r.destination) for r in existing_res}

                    # Filter to only truly new routes
                    new_routes = [
                        RoutePair(origin=origin, destination=dest, is_active=False, error_count=0)
                        for origin, dest in potential_routes
                        if (origin, dest) not in existing
                    ]

                    if new_routes:
                        session.add_all(new_routes)
                        added = len(new_routes)
                        logger.info(f"Adding {added} new routes to DB (Pending Validation)...")
                        await session.commit()
                    else:
                        logger.info("No new routes to add.")

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
        total_validated_count = 0

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

            total_routes = len(routes_objs)
            logger.info(f"Validating {total_routes} routes in chunks...")
            update_job(self.job_id, message=f"Validating {total_routes} routes...")

            routes_data = [
                {"id": r.id, "origin": r.origin, "destination": r.destination}
                for r in routes_objs
            ]

            proxy_mgr = ProxyManager(session)
            await proxy_mgr.load_proxies()

            # Fetch concurrency setting
            setting = await session.execute(select(SystemSetting).where(SystemSetting.key == "scraper_worker_count"))
            setting = setting.scalar_one_or_none()
            val = int(setting.value) if setting and setting.value and setting.value.isdigit() else 50
            max_concurrent = max(1, min(val, 200))

            logger.info(f"Route Scraper: Using {max_concurrent} concurrent workers.")

            # Process in chunks of 4000 to avoid timeout/connection issues
            CHUNK_SIZE = 4000
            chunks = [routes_data[i:i + CHUNK_SIZE] for i in range(0, len(routes_data), CHUNK_SIZE)]

            logger.info(f"Split into {len(chunks)} chunks of up to {CHUNK_SIZE} routes each")

            for chunk_idx, chunk in enumerate(chunks):
                if stop_check and stop_check():
                    logger.info("Validation cancelled by user")
                    break

                chunk_num = chunk_idx + 1
                logger.info(f"Processing chunk {chunk_num}/{len(chunks)} ({len(chunk)} routes)...")
                update_job(self.job_id, message=f"Chunk {chunk_num}/{len(chunks)}: {len(chunk)} routes...")

                validated_count = await self._validate_chunk(
                    chunk, proxy_mgr, max_concurrent, stop_check
                )
                total_validated_count += validated_count

                logger.info(f"Chunk {chunk_num}/{len(chunks)} complete: {validated_count} validated")

                # Small delay between chunks to let connections settle
                if chunk_num < len(chunks):
                    await asyncio.sleep(1)

            logger.info(f"All chunks complete: {total_validated_count} total routes validated")
            return total_validated_count

    async def _validate_chunk(self, chunk, proxy_mgr, max_concurrent, stop_check=None):
        """Validate a chunk of routes (separate client pool per chunk)"""
        sem = asyncio.Semaphore(max_concurrent)
        today_utc = datetime.utcnow()

        # OPTIMIZATION: Authenticate once and share token + HTTP client across all clients
        pool_size = max_concurrent
        logger.info(f"Authenticating once to get shared token...")
        auth_client = FrontierClient(timeout=20.0)
        await auth_client.authenticate()
        shared_token = auth_client.token
        shared_headers = auth_client.headers.copy()
        await auth_client.close()
        logger.info(f"Shared token obtained: {shared_token[:20]}...")

        # Create a single shared httpx client with high connection limits
        logger.info(f"Creating shared HTTP client with connection pooling...")
        limits = httpx.Limits(max_keepalive_connections=pool_size, max_connections=pool_size*2)
        shared_http_client = httpx.AsyncClient(
            limits=limits,
            timeout=20.0,
            verify=True,
            http2=False
        )

        # Create lightweight wrapper clients (no httpx client per wrapper)
        client_pool = []
        for i in range(pool_size):
            client = FrontierClient(timeout=20.0, use_shared_client=True)
            # Inject the shared httpx client
            client.client = shared_http_client
            # Set shared token
            client.token = shared_token
            client.headers = shared_headers.copy()
            client_pool.append(client)

        logger.info(f"Client pool ready with {len(client_pool)} clients (using shared HTTP client)")

        # Track which client to use next (round-robin)
        client_index = 0
        client_lock = asyncio.Lock()

        # Collect results for batch DB updates
        pending_updates = []
        update_lock = asyncio.Lock()

        async def check_route(route_dict):
            nonlocal client_index
            route_id = route_dict["id"]
            origin = route_dict["origin"]
            dest = route_dict["destination"]

            if stop_check and stop_check():
                return {"id": route_id, "status": "skip"}

            result = {"id": route_id, "status": "skip"}

            async with sem:
                # Get a client from the pool (round-robin)
                async with client_lock:
                    client = client_pool[client_index % len(client_pool)]
                    client_index += 1

                check_date = (today_utc + timedelta(days=7)).strftime("%Y-%m-%d")

                # Try request (will retry once on auth error only)
                try:
                    await client.search(origin, dest, check_date)
                    # HTTP 200 = Route exists (valid)
                    logger.info(f"[VALID] {origin}->{dest}")
                    result = {"id": route_id, "status": "valid"}

                except httpx.HTTPStatusError as http_err:
                    if http_err.response.status_code == 400:
                        try:
                            err_data = http_err.response.json()
                            msg = err_data.get("message", "").lower()
                            if "does not exist" in msg:
                                logger.info(f"[INVALID] {origin}->{dest}")
                                result = {"id": route_id, "status": "invalid"}
                        except:
                            pass
                    # Note: Auth error handling removed - shared token is long-lived
                    # If we get auth errors, they'll be logged as skips

                except Exception as e:
                    logger.warning(f"[SKIP] {origin}->{dest} - {type(e).__name__}")

            # Collect result for batch update
            async with update_lock:
                pending_updates.append(result)

                # Batch update every 50 routes to avoid DB pool exhaustion
                if len(pending_updates) >= 50:
                    await flush_updates()

            return result

        async def flush_updates():
            nonlocal pending_updates
            if not pending_updates:
                return

            batch = pending_updates[:]
            pending_updates = []

            valid_ids = [r["id"] for r in batch if r["status"] == "valid"]
            invalid_ids = [r["id"] for r in batch if r["status"] == "invalid"]

            async with SessionLocal() as db_session:
                try:
                    if valid_ids:
                        await db_session.execute(
                            update(RoutePair)
                            .where(RoutePair.id.in_(valid_ids))
                            .values(is_active=True, last_validated=datetime.now(pytz.UTC), error_count=0)
                        )

                    if invalid_ids:
                        await db_session.execute(
                            update(RoutePair)
                            .where(RoutePair.id.in_(invalid_ids))
                            .values(is_active=False, last_validated=datetime.now(pytz.UTC))
                        )

                    await db_session.commit()
                    logger.info(f"Batch updated {len(valid_ids)} valid, {len(invalid_ids)} invalid routes")
                except Exception as e:
                    logger.error(f"Batch DB update failed: {e}")

        # Process chunk
        logger.info(f"Processing {len(chunk)} routes...")

        try:
            # Launch all tasks at once, semaphore controls concurrency
            tasks = [check_route(r) for r in chunk]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Flush any remaining updates
            logger.info("Flushing remaining DB updates...")
            await flush_updates()

            # Count results
            valid_count = 0
            invalid_count = 0
            skip_count = 0

            for res in results:
                if isinstance(res, Exception):
                    logger.error(f"Worker exception: {res}")
                    skip_count += 1
                    continue
                if res["status"] == "valid":
                    valid_count += 1
                elif res["status"] == "invalid":
                    invalid_count += 1
                else:
                    skip_count += 1

            logger.info(f"Chunk complete: {valid_count} Valid, {invalid_count} Invalid, {skip_count} Skipped")
            return valid_count

        except Exception as e:
            logger.error(f"Chunk processing failed: {e}")
            traceback.print_exc()
            return 0

        finally:
            # Clean up shared HTTP client
            logger.info("Closing shared HTTP client...")
            try:
                await shared_http_client.aclose()
            except:
                pass
