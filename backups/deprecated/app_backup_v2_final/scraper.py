import asyncio
import json
import uuid
import base64
import logging
from datetime import datetime, timedelta
import random
import hashlib
from typing import Optional, Dict, Any, List
import httpx
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, delete
import pytz
import os

from app.database import get_db, SessionLocal
from app.models import SystemSetting, Proxy, FlightCache, Airport, RoutePair
from app.compression import compress_data, decompress_data

# Logging setup
logger = logging.getLogger(__name__)

# Active Search Tracker for Deduplication
ACTIVE_SEARCHES = {}

class ProxyManager:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.current_index = 0
        self.proxies = []
        self.enabled = True

    async def load_proxies(self):
        # Check enabled
        enabled_setting = await self.session.execute(select(SystemSetting).where(SystemSetting.key == "proxy_enabled"))
        enabled_setting = enabled_setting.scalar_one_or_none()
        if enabled_setting and enabled_setting.value.lower() == "false":
             self.enabled = False
             return

        # Load proxies from DB and convert to dicts to avoid lazy load issues later
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
        if not self.enabled:
            return None
            
        if not self.proxies:
            return None
        
        # Round robin
        self.current_index = (self.current_index + 1) % len(self.proxies)
        
        # Save index
        await self.save_index()
        
        proxy = self.proxies[self.current_index]
        return f"{proxy['protocol']}://{proxy['url']}"

    async def save_index(self):
        if not self.enabled: return
        
        # Upsert index
        idx_setting = await self.session.execute(select(SystemSetting).where(SystemSetting.key == "proxy_index"))
        idx_setting = idx_setting.scalar_one_or_none()
        if idx_setting:
            idx_setting.value = str(self.current_index)
        else:
            self.session.add(SystemSetting(key="proxy_index", value=str(self.current_index)))
        await self.session.commit()

class FrontierClient:
    BASE_URL = "https://mtier.flyfrontier.com"
    
    def __init__(self, proxy: Optional[str] = None, timeout: float = 30.0, user_agent: str = "NCPAndroid/3.3.0"):
        self.proxy = proxy
        self.timeout = timeout
        self.user_agent = user_agent
        self.headers = {} # Will be populated by authenticate
        self.token = None
        # httpx > 0.20 uses 'proxies' (dict) or 'proxy' (url). 
        # If 'proxy' fails, it might be an old version. 
        # But safest is to pass it as 'proxies' if it's a string, or mount it.
        # Actually, let's just try 'proxies' which is more standard across versions.
        self.client = httpx.AsyncClient(proxies=self.proxy, verify=True, timeout=self.timeout)

    def _generate_uuid(self):
        # Custom UUID generation matching the JS version (modifying specific bytes)
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
        
        # Calculate hash: md5(uuid + vid + timestamp)
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
        # Autonomous Token Generation
        frontier_token = self._generate_frontier_token()
        px_headers = self._generate_px_headers()
        
        initial_headers = {
            'ocp-apim-subscription-key': '493f95d2aa20409e9094b6ae78c1e5de',
            'frontiertoken': frontier_token,
            'user-agent': self.user_agent,
            'accept-encoding': 'gzip'
        }
        initial_headers.update(px_headers)
        
        # Step 1: Get Anonymous Token
        try:
            resp = await self.client.post(f"{self.BASE_URL}/registrationssv/RetrieveAnonymousToken", headers=initial_headers, json=None) # Body is empty/null
            if resp.status_code != 200:
                # Try decoding if it's base64 (as seen in JS code)
                try:
                    decoded = base64.b64decode(resp.content).decode()
                    data = json.loads(decoded)
                except:
                    data = resp.json()
            else:
                data = resp.json()
                
            if 'data' not in data or 'authToken' not in data['data']:
                raise Exception(f"Invalid auth response: {data}")
                
            self.token = data['data']['authToken']
            self.headers = initial_headers.copy()
            self.headers['authtoken'] = self.token
            
        except Exception as e:
            logger.error(f"Auth Step 1 failed: {e}")
            raise

        # Step 2: Assign GoWild Role (MOBA)
        try:
            payload = {
                "roleCode": "MOBA",
                "applicationName": "NCPANDROID",
                "cultureCode": "en-US",
                "newSession": True
            }
            # Need to regenerate PX headers for new request? JS does it.
            self.headers.update(self._generate_px_headers())
            self.headers['content-type'] = 'application/json; charset=utf-8'
            
            resp = await self.client.put(f"{self.BASE_URL}/registrationssv/AssignRole", json=payload, headers=self.headers)
            
            if resp.headers.get('content-encoding') == 'gzip':
                 # httpx handles gzip automatically if configured, but let's be safe
                 pass 
            
            if resp.status_code != 200:
                 try:
                    decoded = base64.b64decode(resp.content).decode()
                    data = json.loads(decoded)
                 except:
                    data = resp.json()
            else:
                data = resp.json()

            if 'data' in data and 'authToken' in data['data']:
                self.token = data['data']['authToken']
                self.headers['authtoken'] = self.token
                logger.debug("Successfully authenticated with MOBA role")
            else:
                raise Exception(f"Role assignment failed: {data}")

        except Exception as e:
            logger.error(f"Auth Step 2 (AssignRole) failed: {e}")
            raise

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
        
        # Update PX headers for search
        self.headers.update(self._generate_px_headers())
        
        try:
            resp = await self.client.post(f"{self.BASE_URL}/flightavailabilityssv/FlightAvailabilitySimpleSearch", json=payload, headers=self.headers)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Search failed for {origin}-{dest} on {date}: {e}")
            raise

    async def close(self):
        await self.client.aclose()

class ScraperEngine:
    def __init__(self):
        pass
        
    async def get_semaphore(self, session: AsyncSession) -> asyncio.Semaphore:
        # Fetch max_concurrent from DB
        setting = await session.execute(select(SystemSetting).where(SystemSetting.key == "max_concurrent_requests"))
        setting = setting.scalar_one_or_none()
        max_conn = int(setting.value) if setting else 2
        return asyncio.Semaphore(max_conn)

    async def is_cache_valid(self, session: AsyncSession, entry: FlightCache) -> bool:
        # Check 1: Absolute Age (Settings)
        setting = await session.execute(select(SystemSetting).where(SystemSetting.key == "cache_duration_minutes"))
        setting = setting.scalar_one_or_none()
        duration_mins = int(setting.value) if setting else 60
        
        created_at = entry.created_at
        if created_at.tzinfo is None:
            created_at = pytz.UTC.localize(created_at)
            
        age = datetime.now(pytz.UTC) - created_at
        if age > timedelta(minutes=duration_mins):
            return False

        # Check 2: "Same Day" Logic (Relaxed)
        # Instead of strict calendar day, just ensure the flight date is still valid (not in the past)
        # The "midnight scraper" might run at 23:55 UTC (Yesterday) for a flight on Today (UTC).
        # Strict calendar matching kills this valid cache.
        # So we trust the 'cache_duration_minutes' to handle staleness, 
        # and only check if the flight itself is in the past.
        
        try:
            flight_date = datetime.strptime(entry.travel_date, "%Y-%m-%d").date()
            
            # Get Origin Timezone
            airport_res = await session.execute(select(Airport).where(Airport.code == entry.origin))
            airport = airport_res.scalar_one_or_none()
            tz_name = airport.timezone if airport else "UTC"
            tz = pytz.timezone(tz_name)
            
            # Current date at origin
            now_origin = datetime.now(tz).date()
            
            # If flight date is in the past relative to origin, it's invalid
            if flight_date < now_origin:
                return False
                
        except Exception as e:
            logger.warning(f"Date validation error: {e}")
            # Fallback: If we can't parse dates, rely strictly on age
            pass

        return True

    async def perform_search(self, origin: str, dest: str, date: str, session: AsyncSession, force_refresh: bool = False):
        # 1. Check Cache (if not forcing refresh)
        if not force_refresh:
            stmt = select(FlightCache).where(
                FlightCache.origin == origin,
                FlightCache.destination == dest,
                FlightCache.travel_date == date
            )
            result = await session.execute(stmt)
            entry = result.scalars().first()
            
            if entry:
                if await self.is_cache_valid(session, entry):
                    # Return format consistent with new structure
                    stats = {"deleted": 0, "added": 0, "errors": 0}
                    return {"flights": decompress_data(entry.data), "stats": stats}
                else:
                    await session.delete(entry) # Remove invalid
                    await session.commit()

        # 2. Request Deduplication / Coalescing
        search_key = f"{origin}-{dest}-{date}"
        if search_key in ACTIVE_SEARCHES:
            logger.debug(f"Joining existing search for {search_key}")
            return await ACTIVE_SEARCHES[search_key]
            
        # 3. Start New Search
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        ACTIVE_SEARCHES[search_key] = future
        
        try:
            result = await self._execute_scrape(origin, dest, date, session)
            if not future.done():
                future.set_result(result)
            return result
        except Exception as e:
            if not future.done():
                future.set_exception(e)
            raise
        finally:
            if search_key in ACTIVE_SEARCHES:
                del ACTIVE_SEARCHES[search_key]

    async def _execute_scrape(self, origin: str, dest: str, date: str, session: AsyncSession):
        # JITTER LOGIC
        try:
            res = await session.execute(select(SystemSetting).where(SystemSetting.key.in_(["scraper_jitter_enabled", "scraper_jitter_min", "scraper_jitter_max"])))
            settings = {s.key: s.value for s in res.scalars().all()}
            
            if settings.get("scraper_jitter_enabled") == "true":
                min_s = float(settings.get("scraper_jitter_min", "1.0"))
                max_s = float(settings.get("scraper_jitter_max", "5.0"))
                delay = random.uniform(min_s, max_s)
                logger.debug(f"Jitter: Sleeping {delay:.2f}s for {origin}->{dest}")
                await asyncio.sleep(delay)
        except Exception as e:
            logger.warning(f"Jitter check failed: {e}")

        stats = {"deleted": 0, "added": 0, "errors": 0}
        
        # Prepare for Scrape
        proxy_mgr = ProxyManager(session)
        await proxy_mgr.load_proxies()
        
        # Load Scraper Settings
        timeout_s = await session.execute(select(SystemSetting).where(SystemSetting.key == "scraper_timeout"))
        timeout_s = timeout_s.scalar_one_or_none()
        timeout = float(timeout_s.value) if timeout_s else 30.0
        
        ua_s = await session.execute(select(SystemSetting).where(SystemSetting.key == "scraper_user_agent"))
        ua_s = ua_s.scalar_one_or_none()
        # Default to requested Android UA if setting is missing or empty
        default_ua = "NCPAndroid/3.3.0"
        ua = ua_s.value if (ua_s and ua_s.value) else default_ua
        
        # Check Concurrency
        sem = await self.get_semaphore(session)
        
        raw_data = None
        last_error = None
        max_retries = 5

        async with sem:
            for attempt in range(max_retries):
                # Rotate proxy on every attempt (if multiple exist)
                proxy_url = await proxy_mgr.get_next_proxy()
                
                client = FrontierClient(proxy=proxy_url, timeout=timeout, user_agent=ua)
                try:
                    if attempt > 0:
                        proxy_info = f"proxy {proxy_url}" if proxy_url else "direct connection"
                        logger.info(f"Retry attempt {attempt+1}/{max_retries} for {origin}->{dest} using {proxy_info}")
                    
                    raw_data = await client.search(origin, dest, date)
                    await client.close()
                    break # Success!
                except Exception as e:
                    await client.close()
                    last_error = e
                    logger.warning(f"Search attempt {attempt+1} failed for {origin}->{dest}: {e}")

                    if attempt < max_retries - 1:
                        # Check if this is a 503 Service Unavailable error
                        is_503 = False
                        if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 503:
                            is_503 = True
                            logger.info(f"503 Service Unavailable detected for {origin}->{dest}, will retry with 5s delay")

                        # Use longer delay for 503 errors (API rate limiting)
                        delay = 5 if is_503 else 1
                        await asyncio.sleep(delay)
            
            if raw_data is None:
                stats["errors"] = 1
                # Check for persistent route errors
                if isinstance(last_error, httpx.HTTPStatusError):
                    status = last_error.response.status_code
                    if status in [400, 403, 404]:
                        stmt = select(RoutePair).where(RoutePair.origin == origin, RoutePair.destination == dest)
                        res = await session.execute(stmt)
                        route = res.scalar_one_or_none()
                        
                        if route:
                            now = datetime.now(pytz.UTC)
                            last_at = route.last_error_at
                            if last_at and last_at.tzinfo is None:
                                last_at = pytz.UTC.localize(last_at)

                            if not last_at or last_at.date() < now.date():
                                route.error_count = (route.error_count or 0) + 1
                                route.last_error_at = now
                                
                                if route.error_count >= 5:
                                    logger.warning(f"Deleting invalid route {origin}-{dest} after 5 failures")
                                    await session.delete(route)
                                else:
                                    logger.info(f"Marking route {origin}-{dest} error {route.error_count}/5")
                                
                                await session.commit()

                return {"error": str(last_error), "stats": stats}

        # Parse Results
        parsed_results = self._parse_response(raw_data, origin, dest)
        logger.debug(f"Parsed {len(parsed_results)} flights from response")

        # DELETE existing entries for this specific route/date to prevent duplicates
        stmt_del = delete(FlightCache).where(
            FlightCache.origin == origin,
            FlightCache.destination == dest,
            FlightCache.travel_date == date
        )
        res_del = await session.execute(stmt_del)
        stats["deleted"] = res_del.rowcount
        
        # Save New
        if parsed_results:
            new_cache = FlightCache(
                origin=origin,
                destination=dest,
                travel_date=date,
                data=compress_data(parsed_results),
                created_at=datetime.now(pytz.UTC)
            )
            session.add(new_cache)
            stats["added"] = len(parsed_results)
        
        await session.commit()

        return {"flights": parsed_results, "stats": stats}

    def _format_duration(self, dur_str: str) -> str:
        # Converts "HH:MM:SS" or "D.HH:MM:SS" to "Xh Ym"
        if not dur_str:
            return "N/A"
        
        days = 0
        time_part = dur_str
        
        # Handle "1.04:05:06" format
        if "." in dur_str and ":" in dur_str:
            parts = dur_str.split(".")
            # Check if the part before dot is numeric (days)
            if len(parts) >= 2 and parts[0].isdigit():
                try:
                    days = int(parts[0])
                    time_part = parts[-1] # Last part should be HH:MM:SS
                except:
                    pass

        if ":" in time_part:
            try:
                parts = time_part.split(':')
                if len(parts) >= 2:
                    h = int(parts[0])
                    m = int(parts[1])
                    
                    total_hours = h + (days * 24)
                    
                    return f"{total_hours}h {m}m"
            except:
                pass
        return dur_str

    def _parse_response(self, data: Dict, origin: str, dest: str) -> List[Dict]:
        flights = []
        try:
            trips = data.get('data', {}).get('results', [])
            fares_map = data.get('data', {}).get('faresAvailable', {})

            logger.debug(f"Parsing response: found {len(trips)} trips")

            for trip in trips:
                for trip_data in trip.get('trips', []):
                    # journeysAvailableByMarket is a DICT, not a LIST
                    # Key: "DEN|LAS", Value: [journey1, journey2, ...]
                    journeys_dict = trip_data.get('journeysAvailableByMarket', {})
                    if not isinstance(journeys_dict, dict): continue

                    for market_key, journeys in journeys_dict.items():
                        logger.debug(f"Market {market_key}: found {len(journeys)} journeys")
                        for journey in journeys:
                            # Check for GoWild
                            gowild_key = None
                            price = None

                            fares = journey.get('fares', [])
                            logger.debug(f"Journey has {len(fares)} fares")

                            for fare in fares:
                                key = fare.get('fareAvailabilityKey')
                                gw_key = fare.get('gowildfareAvailabilityKey')
                                logger.debug("debug")
                                # Check if this fare key corresponds to a GoWild fare?
                                # The instructions say: "If fare.gowildfareAvailabilityKey is NOT NULL"
                                if gw_key:
                                    gowild_key = gw_key
                                    break

                            if not gowild_key:
                                logger.debug("debug")

                            if gowild_key:
                                # Look up price
                                fare_info = fares_map.get(gowild_key)
                                seats_available = None
                                
                                if fare_info and isinstance(fare_info, dict):
                                    price = fare_info.get('totals', {}).get('fareTotal')
                                else:
                                    # Fallback or just log
                                    logger.warning(f"Fare info for key {gowild_key} is missing or invalid type: {type(fare_info)}")
                                    price = 0
                                
                                # Determine O/D from segments (Pre-calculate for logging)
                                f_origin = origin
                                f_dest = dest
                                raw_segments = journey.get('segments', [])
                                if raw_segments:
                                    f_origin = raw_segments[0].get('designator', {}).get('origin') or origin
                                    f_dest = raw_segments[-1].get('designator', {}).get('destination') or dest

                                # Extract Seat Count
                                try:
                                    # 1. Try fare.gowildFareDetails or gowildfaredetails (API case varies)
                                    details = fare.get('gowildFareDetails') or fare.get('gowildfaredetails')
                                    
                                    if details and isinstance(details, list) and len(details) > 0:
                                        seats_available = details[0].get('availableCount')
                                    
                                    # 2. Try fare_info (faresAvailable) if still None
                                    if seats_available is None and fare_info:
                                        # Sometimes it's directly in the fare info? Rare but possible.
                                        pass
                                        
                                    logger.debug(f"Seats for {f_origin}-{f_dest}: {seats_available}")
                                except Exception as e:
                                    logger.warning(f"Failed to extract seats: {e}")

                                # Extract Segments & Layover Info
                                segments = []
                                layover_airports = []
                                has_long_layover = False
                                
                                for i, seg in enumerate(raw_segments):
                                    seg_dur = self._format_duration(seg.get('flyingTime'))
                                    
                                    segment_data = {
                                        "flightNumber": f"{seg.get('identifier', {}).get('carrierCode', '')}{seg.get('identifier', {}).get('identifier', '')}",
                                        "origin": seg.get('designator', {}).get('origin'),
                                        "destination": seg.get('designator', {}).get('destination'),
                                        "departure": seg.get('designator', {}).get('departure'),
                                        "arrival": seg.get('designator', {}).get('arrival'),
                                        "duration": seg_dur
                                    }
                                    
                                    # Calculate layover to next segment
                                    if i < len(raw_segments) - 1:
                                        next_seg = raw_segments[i+1]
                                        try:
                                            # Parse ISO timestamps
                                            arr_str = segment_data["arrival"]
                                            dept_next_str = next_seg.get('designator', {}).get('departure')
                                            
                                            if arr_str and dept_next_str:
                                                # Handle potential Z suffix for UTC (though typically local time in Frontier API)
                                                arr_str = arr_str.replace('Z', '+00:00')
                                                dept_next_str = dept_next_str.replace('Z', '+00:00')

                                                arr_dt = datetime.fromisoformat(arr_str)
                                                dept_next_dt = datetime.fromisoformat(dept_next_str)
                                                
                                                diff = dept_next_dt - arr_dt
                                                minutes = int(diff.total_seconds() / 60)
                                                
                                                hours = minutes // 60
                                                mins = minutes % 60
                                                segment_data["layoverToNext"] = f"{hours}h {mins}m"
                                                segment_data["layoverMinutes"] = minutes
                                                
                                                layover_airports.append(segment_data["destination"])
                                                
                                                if minutes > 360: # 6 hours
                                                    has_long_layover = True
                                            else:
                                                segment_data["layoverToNext"] = "N/A"
                                        except Exception as e:
                                            logger.warning(f"Layover calc failed: {e}")
                                            segment_data["layoverToNext"] = "Unknown"
                                    
                                    segments.append(segment_data)
    
                                stops = len(raw_segments) - 1
                                dept_time = journey.get('designator', {}).get('departure')
                                arr_time = journey.get('designator', {}).get('arrival')
                                
                                # Calculate duration
                                duration_str = self._format_duration(journey.get('totalTripTime'))
                                if not duration_str or duration_str == 'N/A':
                                    try:
                                        d_start = datetime.fromisoformat(dept_time.replace('Z', '+00:00'))
                                        d_end = datetime.fromisoformat(arr_time.replace('Z', '+00:00'))
                                        diff_mins = int((d_end - d_start).total_seconds() / 60)
                                        h = diff_mins // 60
                                        m = diff_mins % 60
                                        duration_str = f"{h}h {m}m"
                                    except:
                                        duration_str = "N/A"

                                # Add "+1 day" indicator
                                try:
                                    d_start_dt = datetime.fromisoformat(dept_time.replace('Z', '+00:00'))
                                    d_end_dt = datetime.fromisoformat(arr_time.replace('Z', '+00:00'))
                                    
                                    # Calculate date difference based on local dates (assuming API returns local times)
                                    days_diff = (d_end_dt.date() - d_start_dt.date()).days
                                    if days_diff > 0:
                                        duration_str += f" (+{days_diff} day{'s' if days_diff > 1 else ''})"
                                except Exception as e:
                                    logger.warning(f"Date diff calc failed: {e}")

                                # (O/D already determined above)

                                flights.append({
                                    "origin": f_origin,
                                    "destination": f_dest,
                                    "price": price,
                                    "seats_available": seats_available,
                                    "stops": stops,
                                    "departure": dept_time,
                                    "arrival": arr_time,
                                    "duration": duration_str,
                                    "is_gowild": True,
                                    "segments": segments,
                                    "layover_airports": layover_airports,
                                    "has_long_layover": has_long_layover
                                })
        except Exception as e:
            logger.error(f"Parsing error: {e}")

        logger.debug(f"Final result: {len(flights)} GoWild flights found")
        return flights

# Helper to verify proxies
async def verify_proxy(proxy_url: str, protocol: str) -> bool:
    # Construct full URL with auth if present in 'proxy_url' (which usually is user:pass@host:port)
    # If protocol is http, full_url is http://...
    full_url = f"{protocol}://{proxy_url}"
    
    # Create proxy dictionary to route ALL traffic through this proxy
    proxies_dict = {
        "http://": full_url,
        "https://": full_url
    }
    
    try:
        # Use httpbin to verify IP masking if possible, or example.com for availability
        # Reduced timeout to 10s for responsiveness
        async with httpx.AsyncClient(proxies=proxies_dict, timeout=10.0, verify=True) as client:
            resp = await client.get("https://www.example.com", follow_redirects=True)
            if resp.status_code == 200:
                return True
            else:
                logger.warning(f"Proxy {proxy_url} failed with status {resp.status_code}")
                return False
    except Exception as e:
        logger.debug(f"Proxy verification failed for {proxy_url}: {e}")
        return False