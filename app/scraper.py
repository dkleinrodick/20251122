import asyncio
import json
import uuid
import base64
import logging
from datetime import datetime, timedelta, date
import random
import hashlib
from typing import Optional, Dict, Any, List, Tuple
import httpx
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, delete, and_
import pytz

from app.database import get_db, SessionLocal
from app.models import SystemSetting, Proxy, FlightCache, Airport, RoutePair, FareSnapshot
from app.compression import compress_data, decompress_data

logger = logging.getLogger(__name__)

class ProxyManager:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.current_index = 0
        self.proxies = []
        self.enabled = True

    async def load_proxies(self):
        # Check enabled
        enabled_setting = await self.session.execute(select(SystemSetting).where(SystemSetting.key == "scraper_proxy_enabled"))
        enabled_setting = enabled_setting.scalar_one_or_none()
        if enabled_setting and enabled_setting.value.lower() == "false":
             self.enabled = False
             return

        result = await self.session.execute(select(Proxy).where(Proxy.is_active == True))
        self.proxies = [{"url": p.url, "protocol": p.protocol} for p in result.scalars().all()]
        
        # Load last index
        idx_setting = await self.session.execute(select(SystemSetting).where(SystemSetting.key == "proxy_index"))
        idx_setting = idx_setting.scalar_one_or_none()
        if idx_setting:
            self.current_index = int(idx_setting.value)
        else:
            self.current_index = 0

    async def get_next_proxy(self) -> Optional[str]:
        if not self.enabled or not self.proxies:
            return None
        
        # Round robin
        self.current_index = (self.current_index + 1) % len(self.proxies)
        
        # Save index (Fire and forget essentially, or batched? simpler to just await)
        # To avoid DB lock contention on every request, maybe we don't save EVERY time?
        # But for 20 workers it's fine.
        return f"{self.proxies[self.current_index]['protocol']}://{self.proxies[self.current_index]['url']}"

class FrontierClient:
    BASE_URL = "https://mtier.flyfrontier.com"
    
    def __init__(self, proxy: Optional[str] = None, timeout: float = 30.0, user_agent: str = "NCPAndroid/3.3.0"):
        self.proxy = proxy
        self.timeout = timeout
        self.user_agent = user_agent
        self.headers = {} 
        self.token = None
        # Configure Proxy
        proxies_arg = None
        if self.proxy:
            proxies_arg = { "http://": self.proxy, "https://": self.proxy }
        
        self.client = httpx.AsyncClient(proxies=proxies_arg, verify=True, timeout=self.timeout)

    def _generate_uuid(self):
        u = uuid.uuid4()
        b = list(u.bytes)
        b[6] = (b[6] & 0x0f) | 0x40
        b[8] = (b[8] & 0x3f) | 0x80
        return uuid.UUID(bytes=bytes(b))

    def _generate_frontier_token(self):
        u = self._generate_uuid()
        prefix = random.randint(1, 9)
        return f"{prefix}{str(u).replace('-', '')}"

    def _generate_px_headers(self):
        u_uuid = str(self._generate_uuid())
        vid = str(self._generate_uuid())
        timestamp = int(datetime.now().timestamp() * 1000)
        hash_input = f"{u_uuid}{vid}{timestamp}"
        h = hashlib.md5(hash_input.encode()).hexdigest()
        px_auth_payload = json.dumps({"u": u_uuid, "v": vid, "t": timestamp, "h": h}, separators=(',', ':'))
        px_auth = "2:" + base64.b64encode(px_auth_payload.encode()).decode()
        device_fp = hashlib.md5(u_uuid.encode()).hexdigest()[:16]

        return {
            'x-px-vid': vid,
            'x-px-uuid': u_uuid,
            'x-px-os': 'Android',
            'x-px-os-version': '16',
            'x-px-device-model': 'sdk_gphone64_x86_64',
            'x-px-mobile-sdk-version': '3.4.5',
            'x-px-authorization': px_auth,
            'x-px-device-fp': device_fp,
            'x-px-hello': 'AgVSBgcKUFAeUAZQBB4CAlUDHgsBUAEeAFcFUVAABlJQClYK'
        }

    async def authenticate(self):
        frontier_token = self._generate_frontier_token()
        px_headers = self._generate_px_headers()
        
        initial_headers = {
            'ocp-apim-subscription-key': '493f95d2aa20409e9094b6ae78c1e5de',
            'frontiertoken': frontier_token,
            'user-agent': self.user_agent,
            'accept-encoding': 'gzip'
        }
        initial_headers.update(px_headers)
        
        try:
            resp = await self.client.post(f"{self.BASE_URL}/registrationssv/RetrieveAnonymousToken", headers=initial_headers, json=None)
            if resp.status_code != 200:
                # Handle Base64 encoded error responses (Frontier quirk)
                try:
                    decoded = base64.b64decode(resp.content).decode()
                    data = json.loads(decoded)
                except:
                    data = resp.json()
                raise Exception(f"Auth failed: {resp.status_code} - {data}")
            else:
                data = resp.json()
                
            if 'data' not in data or 'authToken' not in data['data']:
                raise Exception(f"Invalid auth response: {data}")
                
            self.token = data['data']['authToken']
            self.headers = initial_headers.copy()
            self.headers['authtoken'] = self.token
            
        except Exception as e:
            raise Exception(f"Authentication Error: {str(e)}")

    async def search(self, origin: str, dest: str, date: str) -> Dict:
        if not self.token:
            await self.authenticate()

        payload = {
            "flightAvailabilityRequestModel": {
                "passengers": { "types": [{"type": "ADT", "count": 1}], "residentCountry": "US" },
                "filters": {
                    "maxConnections": 20,
                    "fareInclusionType": "Default",
                    "type": "All"
                },
                "codes": { "currencyCode": "USD" },
                "origin": origin,
                "destination": dest,
                "beginDate": date
            }
        }
        self.headers.update(self._generate_px_headers())
        
        try:
            resp = await self.client.post(f"{self.BASE_URL}/flightavailabilityssv/FlightAvailabilitySimpleSearch", json=payload, headers=self.headers)
            if resp.status_code == 503:
                raise httpx.HTTPStatusError("503 Service Unavailable", request=resp.request, response=resp)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise

    async def close(self):
        await self.client.aclose()

class AsyncScraperEngine:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.proxy_mgr = ProxyManager(session)
        self.settings = {}

    async def load_settings(self):
        # Load all settings at once to reduce DB calls
        res = await self.session.execute(select(SystemSetting))
        self.settings = {s.key: s.value for s in res.scalars().all()}
        await self.proxy_mgr.load_proxies()

    async def _get_setting(self, key: str, default: Any, type_cast=str) -> Any:
        val = self.settings.get(key)
        if val is None: return default
        try:
            return type_cast(val)
        except:
            return default

    async def get_timezone(self, airport_code: str) -> pytz.timezone:
        # Cache this? For now DB lookup is fine.
        res = await self.session.execute(select(Airport).where(Airport.code == airport_code))
        ap = res.scalar_one_or_none()
        return pytz.timezone(ap.timezone) if ap and ap.timezone else pytz.UTC

    async def process_queue(self, tasks: List[Dict[str, str]], mode="auto", ignore_jitter=False, stop_check=None) -> Dict:
        """
        Main entry point.
        tasks: list of dicts {'origin': 'DEN', 'destination': 'MCO', 'date': '2025-11-28'}
        mode: 'auto', 'midnight', 'ondemand'
        """
        await self.load_settings()
        
        worker_count = await self._get_setting("scraper_worker_count", 20, int)
        queue = asyncio.Queue()
        
        # Populate Queue
        for t in tasks:
            queue.put_nowait(t)
            
        results = {"scraped": 0, "skipped": 0, "errors": 0, "details": []}
        
        # Spawn Workers
        workers = []
        for i in range(min(worker_count, len(tasks))):
            workers.append(asyncio.create_task(self._worker(f"Worker-{i}", queue, results, mode, ignore_jitter, stop_check)))
            
        # Wait for queue to empty
        await queue.join()
        
        # Cancel workers
        for w in workers: w.cancel()
        
        return results

    async def _worker(self, name: str, queue: asyncio.Queue, results: Dict, mode: str, ignore_jitter: bool, stop_check=None):
        while True:
            task = await queue.get()
            
            # Check cancellation
            if stop_check and stop_check():
                queue.task_done()
                continue

            origin = task['origin']
            dest = task['destination']
            date_str = task['date']
            
            try:
                # Jitter
                if not ignore_jitter:
                    jitter_min = await self._get_setting("scraper_jitter_min", 0.1, float)
                    jitter_max = await self._get_setting("scraper_jitter_max", 5.0, float)
                    await asyncio.sleep(random.uniform(jitter_min, jitter_max))

                # Check cancellation again after sleep
                if stop_check and stop_check():
                    queue.task_done()
                    continue

                # Proxy
                proxy = await self.proxy_mgr.get_next_proxy()
                
                # Execute
                # NOTE: Creating a new session per worker/request cycle for isolation, 
                # but ideally we'd reuse sessions per worker if we were doing keep-alive. 
                # Given proxy rotation, new session per req is safer.
                
                client = FrontierClient(
                    proxy=proxy, 
                    timeout=await self._get_setting("scraper_timeout", 30.0, float),
                    user_agent=await self._get_setting("scraper_user_agent", "NCPAndroid/3.3.0")
                )
                
                try:
                    # Retry Logic
                    max_retries = 3
                    data = None
                    for attempt in range(max_retries):
                        try:
                            data = await client.search(origin, dest, date_str)
                            break
                        except Exception as e:
                            # Rotate Proxy on failure
                            client.proxy = await self.proxy_mgr.get_next_proxy() 
                            # Re-init client with new proxy? 
                            # httpx client proxy cannot be changed easily after init. 
                            # Recreate client.
                            await client.close()
                            client = FrontierClient(
                                proxy=client.proxy,
                                timeout=client.timeout, 
                                user_agent=client.user_agent
                            )
                            
                            if attempt == max_retries - 1: raise e
                            await asyncio.sleep(1) # Short backoff

                    # Parse
                    flights = self._parse_response(data, origin, dest)
                    
                    # Save
                    # We need a fresh DB session for the worker to avoid async conflicts if sharing 'self.session'
                    # across concurrent tasks isn't managed by a lock. 
                    # SQLAlchemy AsyncSession IS NOT thread-safe, and concurrent use in asyncio tasks 
                    # on the same session object is dangerous.
                    async with SessionLocal() as db:
                        # 1. Save to FlightCache (Live Data)
                        # Delete old
                        await db.execute(delete(FlightCache).where(
                            and_(FlightCache.origin == origin, 
                                 FlightCache.destination == dest, 
                                 FlightCache.travel_date == date_str)
                        ))
                        
                        if flights:
                            db.add(FlightCache(
                                origin=origin, destination=dest, travel_date=date_str,
                                data=compress_data(flights),
                                created_at=datetime.now(pytz.UTC)
                            ))
                            
                        # 2. Save to FareSnapshot (History) - Only if mode is '3week' (Analytics)
                        # Or should we always save snapshot? The requirement was specific to the Three-Week scraper.
                        # But capturing all data is better. Let's just do it if the table exists.
                        # "Output: Writes to two separate database tables." -> Implies always?
                        # Let's restrict to '3week' or explicit flag to avoid DB bloat from frequent AutoScraper runs.

                        if mode == '3week':
                            # Extract summary metrics for ALL fare types
                            lowest_standard = None
                            seats_standard = None
                            lowest_den = None
                            seats_den = None
                            lowest_gowild = None
                            seats_gowild = None

                            for f in flights:
                                # Standard fare
                                if 'standard' in f['fares']:
                                    p = f['fares']['standard']['price']
                                    s = f['fares']['standard']['seats']
                                    if lowest_standard is None or (p is not None and p < lowest_standard):
                                        lowest_standard = p
                                        seats_standard = s

                                # Den discount fare
                                if 'den' in f['fares']:
                                    p = f['fares']['den']['price']
                                    s = f['fares']['den']['seats']
                                    if lowest_den is None or (p is not None and p < lowest_den):
                                        lowest_den = p
                                        seats_den = s

                                # GoWild fare
                                if 'gowild' in f['fares']:
                                    p = f['fares']['gowild']['price']
                                    s = f['fares']['gowild']['seats']
                                    if lowest_gowild is None or (p is not None and p < lowest_gowild):
                                        lowest_gowild = p
                                        seats_gowild = s

                            db.add(FareSnapshot(
                                origin=origin, destination=dest, travel_date=date_str,
                                min_price_standard=lowest_standard,
                                seats_standard=seats_standard,
                                min_price_den=lowest_den,
                                seats_den=seats_den,
                                min_price_gowild=lowest_gowild,
                                seats_gowild=seats_gowild,
                                data=compress_data(flights) # Full blob
                            ))

                        await db.commit()
                    
                    results["scraped"] += 1
                    
                finally:
                    await client.close()

            except Exception as e:
                results["errors"] += 1
                results["details"].append(f"{origin}-{dest}-{date_str}: {str(e)}")
                logger.error(f"Worker failed for {origin}-{dest}: {e}")
            finally:
                queue.task_done()

    def _parse_response(self, data: Dict, origin: str, dest: str) -> List[Dict]:
        flights = []
        try:
            trips = data.get('data', {}).get('results', [])
            fares_map = data.get('data', {}).get('faresAvailable', {})

            for trip in trips:
                for trip_data in trip.get('trips', []):
                    journeys_dict = trip_data.get('journeysAvailableByMarket', {})
                    if not isinstance(journeys_dict, dict): continue

                    for _, journeys in journeys_dict.items():
                        for journey in journeys:
                            fares_list = journey.get('fares', [])
                            if not fares_list: continue
                            
                            # Process Fare Object (First one usually has all keys)
                            fare_obj = fares_list[0]
                            
                            # Mapping: (API Key Field, Details Field, Internal Key)
                            fare_types = [
                                ('standardfareAvailabilityKey', 'standardfaredetails', 'standard'),
                                ('discountdenfareAvailabilityKey', 'discountdenfaredetails', 'den'),
                                ('gowildfareAvailabilityKey', 'gowildfaredetails', 'gowild')
                            ]
                            
                            fares_data = {}
                            
                            for key_field, details_field, internal_key in fare_types:
                                key = fare_obj.get(key_field)
                                if not key: continue
                                
                                # Price
                                price = None
                                f_info = fares_map.get(key)
                                if f_info:
                                    price = f_info.get('totals', {}).get('fareTotal')
                                
                                # Seats
                                seats = None
                                raw_details = fare_obj.get(details_field)
                                if raw_details and isinstance(raw_details, list) and len(raw_details) > 0:
                                    seats = raw_details[0].get('availableCount')
                                    
                                fares_data[internal_key] = { "price": price, "seats": seats }

                            # Skip if no valid fares
                            if not fares_data: continue

                            # Determine Main Price (Lowest)
                            prices = [d['price'] for d in fares_data.values() if d['price'] is not None]
                            main_price = min(prices) if prices else 0
                            
                            # Segments
                            raw_segments = journey.get('segments', [])
                            segments = []
                            layovers = []
                            long_layover = False
                            
                            for i, seg in enumerate(raw_segments):
                                dur_str = self._format_duration(seg.get('flyingTime'))
                                s_data = {
                                    "flightNumber": f"{seg.get('identifier', {}).get('carrierCode')}{seg.get('identifier', {}).get('identifier')}",
                                    "origin": seg.get('designator', {}).get('origin'),
                                    "destination": seg.get('designator', {}).get('destination'),
                                    "departure": seg.get('designator', {}).get('departure'),
                                    "arrival": seg.get('designator', {}).get('arrival'),
                                    "duration": dur_str
                                }
                                
                                # Layover logic
                                if i < len(raw_segments) - 1:
                                    next_s = raw_segments[i+1]
                                    try:
                                        t1 = datetime.fromisoformat(s_data['arrival'].replace('Z', '+00:00'))
                                        t2 = datetime.fromisoformat(next_s.get('designator', {}).get('departure').replace('Z', '+00:00'))
                                        diff_mins = int((t2-t1).total_seconds() / 60)
                                        h, m = divmod(diff_mins, 60)
                                        s_data['layoverToNext'] = f"{h}h {m}m"
                                        layovers.append(s_data['destination'])
                                        if diff_mins > 360: long_layover = True
                                    except: pass
                                
                                segments.append(s_data)

                            # Top Level Info
                            dept = journey.get('designator', {}).get('departure')
                            arr = journey.get('designator', {}).get('arrival')
                            total_dur = self._format_duration(journey.get('totalTripTime'))
                            
                            flights.append({
                                "origin": origin,
                                "destination": dest,
                                "price": main_price,
                                "fares": fares_data, # Rich Data
                                "stops": len(segments) - 1,
                                "departure": dept,
                                "arrival": arr,
                                "duration": total_dur,
                                "is_gowild": 'gowild' in fares_data,
                                "segments": segments,
                                "layover_airports": layovers,
                                "has_long_layover": long_layover
                            })

        except Exception as e:
            logger.error(f"Parse error: {e}")
            
        return flights

    def _format_duration(self, dur: str) -> str:
        # Simple parser for Frontier duration string
        if not dur: return "N/A"
        # Format usually: P0DT4H5M or 4.05:00:00? 
        # Frontier API often returns "PT4H35M". 
        # But previous scraper assumed "HH:MM".
        # Let's stick to the robust logic from previous version if it worked, 
        # or standard ISO duration parsing. 
        # Re-using the logic that seemed to work in legacy:
        if "PT" in dur: # ISO format
             # Placeholder for simple ISO parser if needed, but previous scraper used split(':')
             # If it ain't broke... but let's make it robust.
             return dur.replace("PT", "").replace("H", "h ").replace("M", "m").lower()
        
        return dur # Fallback

class ScraperEngine:
    """
    Wrapper for On-Demand Single Searches (Legacy Compatibility)
    """
    def __init__(self):
        pass

    async def get_semaphore(self, db):
        # Fetch concurrency limit from settings via a temporary engine instance
        engine = AsyncScraperEngine(db)
        limit = await engine._get_setting("scraper_worker_count", 20, int)
        return asyncio.Semaphore(limit)

    async def perform_search(self, origin, dest, date, session: AsyncSession, force_refresh=False, mode="ondemand", ignore_jitter=False, stop_check=None):
        # 1. Check cache if not forced
        if not force_refresh:
            stmt = select(FlightCache).where(
                and_(FlightCache.origin == origin, 
                     FlightCache.destination == dest, 
                     FlightCache.travel_date == date)
            )
            res = await session.execute(stmt)
            entry = res.scalar_one_or_none()
            if entry:
                return {"flights": decompress_data(entry.data)}

        # 2. Use Async Engine logic for single task
        engine = AsyncScraperEngine(session)
        # Note: process_queue handles settings loading
        
        task = {"origin": origin, "destination": dest, "date": date}
        results = await engine.process_queue([task], mode=mode, ignore_jitter=ignore_jitter, stop_check=stop_check)
        
        if results["errors"] > 0:
             # Return error details from the first error
             msg = results["details"][0] if results["details"] else "Unknown error"
             return {"error": msg}
             
        # 3. Fetch freshly cached data
        stmt = select(FlightCache).where(
                and_(FlightCache.origin == origin, 
                     FlightCache.destination == dest, 
                     FlightCache.travel_date == date)
            )
        res = await session.execute(stmt)
        entry = res.scalar_one_or_none()
        if entry:
            return {"flights": decompress_data(entry.data)}
            
        return {"flights": []}

async def verify_proxy(url: str, protocol: str = "http") -> bool:
    try:
        proxies = { "http://": f"{protocol}://{url}", "https://": f"{protocol}://{url}" }
        async with httpx.AsyncClient(proxies=proxies, timeout=10.0) as client:
            resp = await client.get("https://www.google.com", follow_redirects=True)
            return resp.status_code == 200
    except:
        return False
