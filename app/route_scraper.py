import asyncio
import logging
from datetime import datetime, timedelta
import httpx
import re
import json
import pytz
from sqlalchemy import select, or_, and_, update
from app.database import SessionLocal
from app.models import RoutePair, Airport, SystemSetting
from app.airports_data import AIRPORTS_LIST, AIRPORT_MAPPING
from app.scraper import FrontierClient, ProxyManager

logger = logging.getLogger(__name__)

class RouteScraper:
    SITEMAP_URL = "https://flights.flyfrontier.com/en/sitemap/city-to-city-flights/page-1"
    
    def __init__(self):
        pass

    async def run(self):
        logger.info("Starting RouteScraper Job...")
        await self.discover_routes()
        await self.validate_routes()

    async def discover_routes(self):
        logger.info("Step 1: Discovering Routes from Sitemap...")
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(self.SITEMAP_URL)
                if resp.status_code != 200:
                    logger.error(f"Failed to fetch sitemap: {resp.status_code}")
                    return

                content = resp.text
                start_marker = '<script id="__NEXT_DATA__" type="application/json">'
                end_marker = '</script>'
                start_idx = content.find(start_marker)
                if start_idx == -1:
                    logger.error("Could not find NEXT_DATA in sitemap")
                    return
                
                start_idx += len(start_marker)
                end_idx = content.find(end_marker, start_idx)
                if end_idx == -1:
                    logger.error("Could not find end of NEXT_DATA")
                    return
                
                json_str = content[start_idx:end_idx]
                data = json.loads(json_str)
                
                try:
                    links = data['props']['pageProps']['sitemap']['links']
                except KeyError:
                    logger.error("Invalid sitemap JSON structure")
                    return

                potential_routes = set()
                
                for link in links:
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
                    
                    added = 0
                    for origin, dest in potential_routes:
                        if (origin, dest) not in existing:
                            logger.info(f"New potential route found: {origin}-{dest}")
                            session.add(RoutePair(origin=origin, destination=dest, is_active=False, error_count=0))
                            added += 1
                    
                    await session.commit()
                    logger.info(f"Added {added} new routes to DB (Pending Validation).")

        except Exception as e:
            logger.error(f"Discovery failed: {e}")

    def _resolve_city(self, city_name: str) -> list:
        if city_name in AIRPORT_MAPPING:
            return AIRPORT_MAPPING[city_name]
        if "," in city_name:
            simple_city = city_name.split(",")[0].strip()
            if simple_city in AIRPORT_MAPPING:
                return AIRPORT_MAPPING[simple_city]
        return []

    async def validate_routes(self):
        logger.info("Step 2: Validating Pending/Stale Routes...")
        
        async with SessionLocal() as session:
            cutoff = datetime.now(pytz.UTC) - timedelta(days=30)
            
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
                return

            total = len(routes_objs)
            logger.info(f"Validating {total} routes...")
            
            # Detach data needed for scraping to avoid SQLAlchemy async issues in threads
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
            check_date = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")

            async def check_route(route_dict):
                async with sem:
                    proxy = await proxy_mgr.get_next_proxy()
                    client = FrontierClient(proxy=proxy, timeout=20.0)
                    route_id = route_dict["id"]
                    origin = route_dict["origin"]
                    dest = route_dict["destination"]
                    
                    result = {"id": route_id, "status": "skip"}
                    
                    try:
                        try:
                            await client.authenticate() 
                            await client.search(origin, dest, check_date)
                            result["status"] = "valid"
                            logger.info(f"[VALID] {origin}-{dest}")
                        except httpx.HTTPStatusError as http_err:
                            if http_err.response.status_code == 400:
                                result["status"] = "invalid"
                                logger.warning(f"[INVALID] {origin}-{dest} (400)")
                            else:
                                logger.warning(f"[SKIP] {origin}-{dest} ({http_err.response.status_code})")
                        except Exception as e:
                            logger.error(f"[ERROR] {origin}-{dest}: {e}")
                    finally:
                        await client.close()
                    
                    return result

            # Process in batches
            batch_size = 50 
            for i in range(0, total, batch_size):
                batch = routes_data[i:i + batch_size]
                logger.info(f"Processing validation batch {i+1}-{min(i+batch_size, total)} of {total}")
                
                tasks = [check_route(r) for r in batch]
                results = await asyncio.gather(*tasks)
                
                # Bulk Update Logic
                now = datetime.now(pytz.UTC)
                
                valid_ids = [r["id"] for r in results if r["status"] == "valid"]
                invalid_ids = [r["id"] for r in results if r["status"] == "invalid"]
                
                if valid_ids:
                    await session.execute(
                        update(RoutePair)
                        .where(RoutePair.id.in_(valid_ids))
                        .values(is_active=True, last_validated=now, error_count=0)
                    )
                
                if invalid_ids:
                    await session.execute(
                        update(RoutePair)
                        .where(RoutePair.id.in_(invalid_ids))
                        .values(is_active=False, last_validated=now)
                        # error_count? Optional.
                    )

                await session.commit()
                
            logger.info("Validation complete.")
