from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Request, Form, Header
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.future import select
from sqlalchemy import delete, update, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
import json
import uuid
import logging
from datetime import datetime, timedelta
from typing import List, Optional
import pytz
import httpx
import re

from app.database import init_db, get_db, engine, Base, SessionLocal
from app.models import SystemSetting, Proxy, FlightCache, RoutePair, Airport, WeatherData
from app.scraper import ScraperEngine, verify_proxy
from app.airports_data import AIRPORT_MAPPING, AIRPORTS_LIST
# Scheduler disabled
SCRAPER_STATUS = {"status": "disabled"}
from app.search_logic import find_round_trip_same_day, build_multi_hop_route, get_map_data
from app.compression import decompress_data

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(name)s - %(message)s"
)
# Scheduler disabled for serverless - no apscheduler
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("tzlocal").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

app = FastAPI(title="WildFares")

# Static files disabled for serverless - use CDN or public folder instead
# app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Global Job Store (In-memory for simplicity)
JOBS = {}
STARTUP_TIME = datetime.utcnow()

@app.on_event("startup")
async def startup_event():
    """Lightweight startup for serverless - with essential seeding."""
    global STARTUP_TIME
    STARTUP_TIME = datetime.utcnow()

    try:
        async with SessionLocal() as session:
            # Check and seed airports if missing
            result = await session.execute(select(func.count()).select_from(Airport))
            count = result.scalar()
            if count == 0:
                logger.info(f"Seeding {len(AIRPORTS_LIST)} airports...")
                from sqlalchemy import insert
                to_insert = []
                for a in AIRPORTS_LIST:
                     to_insert.append({
                         "code": a["code"], 
                         "city_name": a["city"],
                         "timezone": a.get("timezone", "UTC"),
                         "latitude": a.get("lat"),
                         "longitude": a.get("lon")
                     })
                if to_insert:
                     await session.execute(insert(Airport).values(to_insert))
                     await session.commit()
                logger.info("Airports seeded successfully.")
            else:
                logger.info(f"Airports already seeded ({count} found).")
                
    except Exception as e:
        logger.warning(f"Startup seeding check failed: {e}")

    logger.info("Background scheduler disabled for Vercel - using GitHub Actions instead")

async def verify_admin(x_admin_pass: str = Header(None), db: AsyncSession = Depends(get_db)):
    if not x_admin_pass:
        logger.warning("Verify Admin: Missing Header")
        raise HTTPException(status_code=401, detail="Missing Admin Password")
    
    try:
        res = await db.execute(select(SystemSetting).where(SystemSetting.key == "admin_password"))
        setting = res.scalar_one_or_none()
        stored_pass = setting.value if setting else "84798479Aa!"
        
        if x_admin_pass != stored_pass:
            logger.warning(f"Verify Admin: Failed. Provided: '{x_admin_pass}' vs Stored: '{stored_pass}'")
            raise HTTPException(status_code=401, detail="Invalid Admin Password")
        return True
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Verify Admin DB Error: {e}")
        raise HTTPException(status_code=500, detail=f"Database Auth Error: {str(e)}")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/features", response_class=HTMLResponse)
async def read_features(request: Request):
    return templates.TemplateResponse("marketing.html", {"request": request})

@app.get("/terms", response_class=HTMLResponse)
async def read_terms(request: Request):
    return templates.TemplateResponse("terms.html", {"request": request})

@app.get("/privacy", response_class=HTMLResponse)
async def read_privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})

@app.get("/admin", response_class=HTMLResponse)
async def read_admin(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})

# API Routes

@app.get("/api/ping")
async def ping():
    """Simple health check endpoint without database dependency"""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}

@app.get("/api/debug")
async def debug_db():
    """Debug database connection with enhanced diagnostics"""
    import os
    import traceback
    import socket
    import asyncpg
    import ssl
    
    debug_info = {
        "database_url_set": False,
        "database_url_preview": "N/A",
        "dns_resolution": {},
        "direct_asyncpg_test": "Not attempted",
        "sqlalchemy_test": "Not attempted",
        "errors": []
    }

    try:
        db_url = os.environ.get("DATABASE_URL", "NOT_SET")
        debug_info["database_url_set"] = db_url != "NOT_SET"
        
        if "@" in db_url:
            user_part = db_url.split("@")[0]
            host_part = db_url.split("@")[1]
            masked = user_part.split(":")[0] + ":****@" + host_part
            debug_info["database_url_preview"] = masked
            
            # Extract host for DNS test
            try:
                host = host_part.split(":")[0]
                if "/" in host: host = host.split("/")[0]
                
                debug_info["dns_resolution"]["host"] = host
                # Resolve IPv4
                debug_info["dns_resolution"]["ipv4"] = [ip[4][0] for ip in socket.getaddrinfo(host, None, socket.AF_INET)]
            except Exception as e:
                debug_info["dns_resolution"]["error"] = str(e)

            # Direct asyncpg test
            try:
                debug_info["direct_asyncpg_test"] = "Connecting..."
                
                # Parse URL manually to be sure
                # postgresql://user:pass@host:port/db
                from urllib.parse import urlparse
                p = urlparse(db_url)
                
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
                
                conn = await asyncpg.connect(
                    user=p.username,
                    password=p.password,
                    host=p.hostname,
                    port=p.port,
                    database=p.path.lstrip('/'),
                    ssl=ssl_ctx,
                    server_settings={'jit': 'off'}
                )
                version = await conn.fetchval('SELECT version()')
                await conn.close()
                debug_info["direct_asyncpg_test"] = f"SUCCESS! Version: {version}"
            except Exception as e:
                debug_info["direct_asyncpg_test"] = f"FAILED: {str(e)}"
                debug_info["errors"].append(f"AsyncPG Error: {traceback.format_exc()}")

        # Try SQLAlchemy (Existing logic)
        try:
            from app.database import engine
            from sqlalchemy import text
            async with engine.begin() as conn:
                result = await conn.execute(text("SELECT 1"))
            debug_info["sqlalchemy_test"] = "OK"
        except Exception as e:
            debug_info["sqlalchemy_test"] = "FAILED"
            debug_info["errors"].append(f"SQLAlchemy Error: {str(e)}")

        # Try to read settings if DB works
        if debug_info["sqlalchemy_test"] == "OK":
            async with SessionLocal() as session:
                res = await session.execute(select(SystemSetting).where(SystemSetting.key == "admin_password"))
                setting = res.scalar_one_or_none()
                debug_info["stored_admin_password"] = setting.value if setting else "admin (default)"

    except Exception as e:
        debug_info["errors"].append(f"General Error: {str(e)}")
        debug_info["traceback"] = traceback.format_exc()

    return debug_info

@app.post("/api/seed")
async def seed_database(admin: bool = Depends(verify_admin), db: AsyncSession = Depends(get_db)):
    """Seed airports table from AIRPORTS_LIST"""
    from sqlalchemy import insert

    # Check if airports already exist
    result = await db.execute(select(func.count()).select_from(Airport))
    count = result.scalar()

    if count > 0:
        return {"status": "skipped", "message": f"Airports already seeded ({count} airports exist)"}

    # Insert airports from AIRPORTS_LIST
    airports_to_insert = []
    for airport_data in AIRPORTS_LIST:
        airports_to_insert.append({
            "code": airport_data["code"],
            "city_name": airport_data["city"],
            "timezone": airport_data["timezone"],
            "latitude": airport_data["lat"],
            "longitude": airport_data["lon"]
        })

    if airports_to_insert:
        stmt = insert(Airport).values(airports_to_insert)
        await db.execute(stmt)
        await db.commit()

    return {"status": "success", "message": f"Seeded {len(airports_to_insert)} airports"}

@app.get("/api/locations")
async def get_locations(db: AsyncSession = Depends(get_db)):
    try:
        res = await db.execute(select(Airport).order_by(Airport.city_name))
        airports = res.scalars().all()
        
        # Build map for is_international
        intl_codes = {a['code'] for a in AIRPORTS_LIST if a.get('is_international')}
        
        # Include City for grouping and Timezone for date logic
        return [
            {
                "code": a.code,
                "name": f"{a.city_name} [{a.code}]" if a.city_name else a.code,
                "city": a.city_name,
                "timezone": a.timezone,
                "is_international": a.code in intl_codes
            }
            for a in airports
        ]
    except Exception as e:
        import traceback
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "traceback": traceback.format_exc()}
        )

@app.get("/api/routes")
async def get_routes(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(RoutePair).where(RoutePair.is_active == True))
    pairs = res.scalars().all()
    routes = {}
    for p in pairs:
        if p.origin not in routes:
            routes[p.origin] = []
        if p.destination not in routes[p.origin]:
            routes[p.origin].append(p.destination)
    return routes

@app.get("/api/search")
async def search_route(origin: str, destination: str, date: str, force_refresh: bool = False, db: AsyncSession = Depends(get_db)):
    engine = ScraperEngine()
    
    # Check mode
    mode_res = await db.execute(select(SystemSetting).where(SystemSetting.key == "scraper_mode"))
    mode_setting = mode_res.scalar_one_or_none()
    mode = mode_setting.value if mode_setting else "ondemand"
    
    if mode == "automatic":
        stmt = select(FlightCache).where(
            FlightCache.origin == origin, 
            FlightCache.destination == destination,
            FlightCache.travel_date == date
        )
        res = await db.execute(stmt)
        entry = res.scalars().first()
        return decompress_data(entry.data) if entry else []

    result = await engine.perform_search(origin.upper(), destination.upper(), date, db, force_refresh)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    
    # Return just the list of flights for the frontend
    return result.get("flights", [])

@app.get("/api/roundtrip")
async def search_roundtrip(origin: str, date: str, destination: Optional[str] = None, min_hours: int = 4, db: AsyncSession = Depends(get_db)):
    return await find_round_trip_same_day(db, origin, destination, date, min_hours)

@app.get("/api/builder")
async def search_builder(origin: str, destination: str, date: str, min_layover: float = 1.0, max_layover: float = 6.0, max_duration: float = 24.0, max_stops: int = 3, db: AsyncSession = Depends(get_db)):
    return await build_multi_hop_route(db, origin, destination, date, min_layover, max_layover, max_duration, max_stops)

@app.get("/api/map_data")
async def search_map_data(date: str, db: AsyncSession = Depends(get_db)):
    return await get_map_data(db, date)

@app.get("/api/explore_advanced")
async def explore_advanced(date: str, target: Optional[str] = None, type: str = "from", db: AsyncSession = Depends(get_db)):
    """
    type: 'from' or 'to'
    target: Optional Airport Code (if none, return all)
    """
    query = select(FlightCache).where(FlightCache.travel_date == date)
    
    if target:
        if type == 'from':
            query = query.where(FlightCache.origin == target)
        else:
            query = query.where(FlightCache.destination == target)
        
    res = await db.execute(query)
    entries = res.scalars().all()
    
    results = []
    for e in entries:
        results.extend(decompress_data(e.data))
        
    return results

# --- Job / Bulk Logic ---

async def process_bulk_search(job_id: str, tasks: List[dict]):
    scraper = ScraperEngine()
    total = len(tasks)
    completed = 0
    JOBS[job_id] = {"status": "running", "progress": 0, "results": [], "total": total}
    async with AsyncSession(engine) as db:
        sem = await scraper.get_semaphore(db)
    async def limited_task(t):
        async with sem:
            async with AsyncSession(engine) as local_db:
                res = await scraper.perform_search(t['origin'], t['destination'], t['date'], local_db)
                # Extract flights from result dict
                data = res.get("flights", []) if isinstance(res, dict) else []
                return {**t, "data": data}
    def chunked(l, n):
        for i in range(0, len(l), n): yield l[i:i + n]
    
    for chunk in chunked(tasks, 5):
        coros = [limited_task(t) for t in chunk]
        chunk_results = await asyncio.gather(*coros)
        JOBS[job_id]["results"].extend(chunk_results)
        completed += len(chunk)
        JOBS[job_id]["progress"] = int((completed / total) * 100)
        await asyncio.sleep(0.1)
    JOBS[job_id]["status"] = "completed"

@app.post("/api/bulk_search")
async def trigger_bulk_search(request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    data = await request.json()
    mode = data.get("mode") 
    origin = data.get("origin")
    date = data.get("date")
    tasks = []
    if mode == "broadcast":
        res = await db.execute(select(RoutePair.destination).where(RoutePair.origin == origin))
        dests = res.scalars().all()
        if not dests:
             res_all = await db.execute(select(Airport.code))
             all_codes = res_all.scalars().all()
             dests = [c for c in all_codes if c != origin]
        for d in dests: tasks.append({"origin": origin, "destination": d, "date": date})
    elif mode == "all":
        res = await db.execute(select(RoutePair))
        pairs = res.scalars().all()
        if not pairs:
             res_all = await db.execute(select(Airport.code))
             all_codes = res_all.scalars().all()
             hubs = ["DEN", "MCO", "ATL", "LAS", "ORD", "PHL"]
             for h in hubs:
                 if h in all_codes:
                     for dest in all_codes:
                         if h != dest: tasks.append({"origin": h, "destination": dest, "date": date})
        else:
            for p in pairs: tasks.append({"origin": p.origin, "destination": p.destination, "date": date})

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "pending", "progress": 0, "results": []}
    background_tasks.add_task(process_bulk_search, job_id, tasks)
    return {"job_id": job_id}

@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job: raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.get("/api/scraper/status")
async def get_scraper_status(db: AsyncSession = Depends(get_db)):
    # Inject popup setting
    res = await db.execute(select(SystemSetting).where(SystemSetting.key == "scraper_popup_enabled"))
    setting = res.scalar_one_or_none()
    enabled = setting.value.lower() == "true" if setting else True
    
    response = SCRAPER_STATUS.copy()
    response["popup_enabled"] = enabled
    return response

@app.get("/api/admin/uptime")
async def get_uptime():
    now = datetime.utcnow()
    diff = now - STARTUP_TIME
    return {
        "uptime_seconds": diff.total_seconds(),
        "startup_time": STARTUP_TIME.isoformat()
    }

@app.get("/api/public_config")
async def get_public_config(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(SystemSetting).where(
        or_(SystemSetting.key == "announcement_enabled", SystemSetting.key == "announcement_html")
    ))
    settings = {s.key: s.value for s in res.scalars().all()}
    return settings

# Admin API
@app.get("/api/settings", dependencies=[Depends(verify_admin)])
async def get_settings(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(SystemSetting))
    return {s.key: s.value for s in res.scalars().all()}

@app.post("/api/settings", dependencies=[Depends(verify_admin)])
async def update_settings(request: Request, db: AsyncSession = Depends(get_db)):
    data = await request.json()
    reschedule_weather = False
    reschedule_scraper = False
    
    for k, v in data.items():
        # Check for Debug Logging
        if k == "debug_logging_enabled":
            if str(v).lower() == "true":
                logging.getLogger().setLevel(logging.DEBUG)
                logger.info("Debug logging ENABLED")
            else:
                logging.getLogger().setLevel(logging.INFO)
                logger.info("Debug logging DISABLED")

        # Check for Weather Change
        if k == "weather_scrape_time":
            res = await db.execute(select(SystemSetting).where(SystemSetting.key == k))
            current = res.scalar_one_or_none()
            if not current or current.value != str(v):
                reschedule_weather = True
                
        # Check for Scraper Interval Change
        if k == "auto_scrape_interval" or k == "auto_scrape_enabled":
            res = await db.execute(select(SystemSetting).where(SystemSetting.key == k))
            current = res.scalar_one_or_none()
            if not current or current.value != str(v):
                reschedule_scraper = True
        
        stmt = select(SystemSetting).where(SystemSetting.key == k)
        res = await db.execute(stmt)
        setting = res.scalar_one_or_none()
        if setting: setting.value = str(v)
        else: db.add(SystemSetting(key=k, value=str(v)))
    
    await db.commit()
    
    # Scheduler logic removed
    pass
        
    return {"status": "ok"}

import os
import signal
import asyncio

@app.post("/api/admin/reboot", dependencies=[Depends(verify_admin)])
async def reboot_backend():
    # Schedule the kill to happen shortly after response is sent
    # We use os._exit(1) to force immediate termination, which ensures 
    # the batch file loop catches it and restarts the process.
    def delayed_kill():
        import time
        time.sleep(1)
        os._exit(1)
        
    # Use asyncio loop to schedule it without blocking the return
    asyncio.get_event_loop().run_in_executor(None, delayed_kill)
    
    return {"status": "rebooting"}

@app.get("/api/proxies", dependencies=[Depends(verify_admin)])
async def get_proxies(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Proxy))
    return res.scalars().all()

@app.post("/api/proxies", dependencies=[Depends(verify_admin)])
async def add_proxies(request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    data = await request.json()
    raw_proxies = data.get("proxies", "").splitlines()
    for p in raw_proxies:
        p = p.strip()
        if not p: continue
        colon_parts = p.split(':')
        if len(colon_parts) == 4 and "://" not in p:
            host, port, user, password = colon_parts
            url_body = f"{user}:{password}@{host}:{port}"
            proto = "http"
        else:
            parts = p.split("://")
            if len(parts) == 2: proto, url_body = parts
            else: proto, url_body = "http", parts[0]
        res = await db.execute(select(Proxy).where(Proxy.url == url_body))
        if not res.scalar_one_or_none():
            db.add(Proxy(url=url_body, protocol=proto, is_active=False))
    await db.commit()
    background_tasks.add_task(check_all_proxies)
    return {"status": "added"}

async def check_all_proxies():
    # 1. Get all IDs first
    async with AsyncSession(engine) as db:
        res = await db.execute(select(Proxy.id, Proxy.url, Proxy.protocol))
        proxies_data = res.all() # List of tuples: (id, url, protocol)

    # 2. Iterate and update individually
    for pid, url, protocol in proxies_data:
        try:
            is_valid = await verify_proxy(url, protocol)
            
            async with AsyncSession(engine) as db:
                stmt = select(Proxy).where(Proxy.id == pid)
                res = await db.execute(stmt)
                p = res.scalar_one_or_none()
                if p:
                    p.is_active = is_valid
                    p.last_checked = datetime.utcnow()
                    await db.commit()
        except Exception as e:
            logger.error(f"Error checking proxy {pid}: {e}")

@app.delete("/api/proxies/{proxy_id}", dependencies=[Depends(verify_admin)])
async def delete_proxy(proxy_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(Proxy).where(Proxy.id == proxy_id))
    await db.commit()
    return {"status": "deleted"}

@app.delete("/api/proxies", dependencies=[Depends(verify_admin)])
async def delete_all_proxies(db: AsyncSession = Depends(get_db)):
    await db.execute(delete(Proxy))
    await db.commit()
    return {"status": "deleted_all"}

@app.post("/api/admin/clear_cache", dependencies=[Depends(verify_admin)])
async def clear_flight_cache(db: AsyncSession = Depends(get_db)):
    await db.execute(delete(FlightCache))
    await db.commit()
    return {"status": "cache_cleared"}

async def validate_routes_task(job_id: str):
    JOBS[job_id] = {"status": "running", "progress": 0, "message": "Starting validation..."}
    
    # 1. Fetch all route data (detached)
    routes_data = []
    async with SessionLocal() as session:
        res = await session.execute(select(RoutePair.id, RoutePair.origin, RoutePair.destination))
        routes_data = res.all()

    total = len(routes_data)
    if total == 0:
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["message"] = "No routes to validate."
        return

    today = datetime.utcnow().strftime("%Y-%m-%d")
    tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    engine = ScraperEngine()
    async with SessionLocal() as tmp_session:
        sem = await engine.get_semaphore(tmp_session)

    # Progress tracking
    completed_count = 0
    active_count = 0
    bad_count = 0

    async def validate_single(rid, origin, destination):
        nonlocal completed_count, active_count, bad_count
        try:
            async with sem:
                async with SessionLocal() as session:
                    # Perform search (network intensive)
                    res = await engine.perform_search(origin, destination, today, session, force_refresh=True)
                    
                    is_valid = True
                    if isinstance(res, dict) and "error" in res:
                        err_msg = res["error"]
                        # Treat 400, 403, 404, 500 as potentially invalid or retryable
                        # 400 often means 'Route invalid' or 'No flights' for this date
                        if any(x in err_msg for x in ["400", "403", "404", "500"]):
                            # Retry with tomorrow's date to be sure
                            res2 = await engine.perform_search(origin, destination, tomorrow, session, force_refresh=True)
                            if isinstance(res2, dict) and "error" in res2:
                                is_valid = False # Both failed -> Invalid
                            else:
                                is_valid = True # Tomorrow worked -> Valid
                        else:
                            # Other errors (timeouts, etc) might be temporary, but let's default to Valid to be safe
                            # Or maybe Invalid? Let's keep Valid for now unless explicit error.
                            is_valid = True
                    
                    # Update DB (short lived)
                    stmt = select(RoutePair).where(RoutePair.id == rid)
                    r_res = await session.execute(stmt)
                    route = r_res.scalar_one_or_none()
                    if route:
                        route.is_active = is_valid
                        if not is_valid:
                            route.error_count += 1
                            bad_count += 1
                        else:
                            route.error_count = 0
                            active_count += 1
                        await session.commit()
        except Exception as e:
            logger.error(f"Validation failed for route {rid}: {e}")
            bad_count += 1 # Assume bad if exception? or just skip counting? Let's count as bad.
        finally:
            completed_count += 1
            # Update shared job state occasionally or always
            progress = int((completed_count / total) * 100)
            JOBS[job_id]["progress"] = progress
            JOBS[job_id]["message"] = f"Validated {completed_count}/{total} (Active: {active_count}, Bad: {bad_count})"

    # Launch all tasks
    tasks = [validate_single(rid, origin, destination) for rid, origin, destination in routes_data]
    await asyncio.gather(*tasks)

    JOBS[job_id]["status"] = "completed"
    JOBS[job_id]["message"] = f"Validation complete. Active: {active_count}, Bad: {bad_count}, Total: {total}"

async def run_full_cache_task(job_id: str, scope: str):
    JOBS[job_id] = {"status": "running", "progress": 0, "message": "Starting full cache..."}
    
    # 1. Fetch Active Routes
    routes_data = []
    async with SessionLocal() as session:
        res = await session.execute(select(RoutePair.origin, RoutePair.destination).where(RoutePair.is_active == True))
        routes_data = res.all()

    total_routes = len(routes_data)
    if total_routes == 0:
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["message"] = "No active routes to cache."
        return

    # Determine dates
    dates_to_scrape = []
    today = datetime.utcnow().strftime("%Y-%m-%d")
    tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    if scope == 'today':
        dates_to_scrape.append(today)
    elif scope == 'tomorrow':
        dates_to_scrape.append(tomorrow)
    else:
        dates_to_scrape = [today, tomorrow]
        
    total_tasks = total_routes * len(dates_to_scrape)
    
    engine = ScraperEngine()
    async with SessionLocal() as tmp_session:
        sem = await engine.get_semaphore(tmp_session)

    completed_count = 0
    
    async def cache_single(origin, destination, date):
        nonlocal completed_count
        try:
            async with sem:
                async with SessionLocal() as session:
                    # Force refresh = True implies "Scrape and Cache"
                    await engine.perform_search(origin, destination, date, session, force_refresh=True)
        except Exception as e:
            logger.error(f"Cache failed for {origin}->{destination} on {date}: {e}")
        finally:
            completed_count += 1
            progress = int((completed_count / total_tasks) * 100)
            JOBS[job_id]["progress"] = progress
            JOBS[job_id]["message"] = f"Cached {completed_count}/{total_tasks}"

    # Build task list
    tasks = []
    for origin, destination in routes_data:
        for date in dates_to_scrape:
            tasks.append(cache_single(origin, destination, date))
            
    await asyncio.gather(*tasks)

    JOBS[job_id]["status"] = "completed"
    JOBS[job_id]["message"] = f"Full Cache Complete. Processed {completed_count} searches."

@app.post("/api/admin/force_cache", dependencies=[Depends(verify_admin)])
async def force_cache(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    scope = data.get("scope", "both")
    job_id = str(uuid.uuid4())
    background_tasks.add_task(run_full_cache_task, job_id, scope)
    return {"status": "started", "job_id": job_id}

async def scrape_frontier_routes(db: AsyncSession):
    url = "https://flights.flyfrontier.com/en/sitemap/city-to-city-flights/page-1"
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            resp = await client.get(url)
            content = resp.text
            start_marker = '<script id="__NEXT_DATA__" type="application/json">'
            end_marker = '</script>'
            start_idx = content.find(start_marker)
            if start_idx == -1: return 0
            start_idx += len(start_marker)
            end_idx = content.find(end_marker, start_idx)
            if end_idx == -1: return 0
            data = json.loads(content[start_idx:end_idx])
            try: links = data['props']['pageProps']['sitemap']['links']
            except KeyError: return 0
            
            existing_routes = set()
            res = await db.execute(select(RoutePair))
            for r in res.scalars().all(): existing_routes.add((r.origin, r.destination))
            new_count = 0
            for link in links:
                route_name = link.get('name', '')
                if " - " in route_name:
                    parts = route_name.split(" - ")
                    if len(parts) == 2:
                        origin_name = parts[0].strip()
                        dest_name = parts[1].strip()
                        origin_codes = AIRPORT_MAPPING.get(origin_name, [])
                        if not origin_codes and ", " in origin_name: origin_codes = AIRPORT_MAPPING.get(origin_name.split(",")[0].strip(), [])
                        dest_codes = AIRPORT_MAPPING.get(dest_name, [])
                        if not dest_codes and ", " in dest_name: dest_codes = AIRPORT_MAPPING.get(dest_name.split(",")[0].strip(), [])
                        for o_code in origin_codes:
                            for d_code in dest_codes:
                                if (o_code, d_code) not in existing_routes:
                                    db.add(RoutePair(origin=o_code, destination=d_code, is_active=True))
                                    new_count += 1
                                    existing_routes.add((o_code, d_code))
            await db.commit()
            return len(existing_routes)
    except Exception as e: return 0

async def update_weather_task():
    logger.info("Updating Weather Data...")
    async with SessionLocal() as session:
        async with httpx.AsyncClient() as client:
            for idx, entry in enumerate(AIRPORTS_LIST):
                code = entry["code"]
                lat = entry.get("lat")
                lon = entry.get("lon")
                
                if not lat or not lon: continue
                
                try:
                    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=weathercode,temperature_2m_max&timezone=auto&forecast_days=16"
                    resp = await client.get(url, timeout=10.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        daily = data.get("daily", {})
                        
                        times = daily.get("time", [])
                        codes = daily.get("weathercode", [])
                        temps = daily.get("temperature_2m_max", [])
                        
                        for i, date_str in enumerate(times):
                            stmt = select(WeatherData).where(WeatherData.airport_code == code, WeatherData.date == date_str)
                            res = await session.execute(stmt)
                            wd = res.scalar_one_or_none()
                            
                            if not wd:
                                wd = WeatherData(airport_code=code, date=date_str, condition_code=codes[i], temp_high=temps[i])
                                session.add(wd)
                            else:
                                wd.condition_code = codes[i]
                                wd.temp_high = temps[i]
                                wd.updated_at = datetime.utcnow()
                        
                        await session.commit()
                except Exception as e:
                    logger.error(f"Weather update failed for {code}: {e}")
    logger.info("Weather Update Completed")

@app.post("/api/update_routes", dependencies=[Depends(verify_admin)])
async def update_routes(background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    count = await scrape_frontier_routes(db)
    job_id = str(uuid.uuid4())
    background_tasks.add_task(validate_routes_task, job_id)
    return {"status": "started", "job_id": job_id, "routes_found": count}

@app.post("/api/admin/update_weather", dependencies=[Depends(verify_admin)])
async def trigger_weather_update(background_tasks: BackgroundTasks):
    background_tasks.add_task(update_weather_task)
    return {"status": "started"}

async def run_manual_scrape_task(job_id: str):
    """Manually triggered full scrape task - using robust cache logic"""
    JOBS[job_id] = {"status": "running", "progress": 0, "message": "Starting manual scrape..."}
    
    try:
        # 1. Fetch Active Routes
        async with SessionLocal() as session:
            res = await session.execute(select(RoutePair).where(RoutePair.is_active == True))
            routes = res.scalars().all()
            # Detach data
            routes_data = [(r.origin, r.destination) for r in routes]

        if not routes_data:
            JOBS[job_id]["status"] = "completed"
            JOBS[job_id]["message"] = "No active routes found."
            return

        # Identify International Airports
        intl_codes = {a['code'] for a in AIRPORTS_LIST if a.get('is_international')}

        engine = ScraperEngine()
        async with SessionLocal() as tmp_session:
            sem = await engine.get_semaphore(tmp_session)

        completed_count = 0
        
        async def scrape_single(origin, destination, date):
            nonlocal completed_count
            try:
                async with sem:
                    async with SessionLocal() as session:
                        # Perform search and update cache
                        await engine.perform_search(origin, destination, date, session, force_refresh=True)
            except Exception as e:
                logger.error(f"Manual scrape failed for {origin}->{destination} on {date}: {e}")
            finally:
                completed_count += 1
                if total_tasks > 0:
                    progress = int((completed_count / total_tasks) * 100)
                    JOBS[job_id]["progress"] = progress
                    JOBS[job_id]["message"] = f"Scraped {completed_count}/{total_tasks}"

        # Build Task List with Dynamic Windows
        tasks = []
        now = datetime.utcnow()
        
        for origin, destination in routes_data:
            # Determine window size
            window = 2 # Default: Today + Tomorrow
            if origin in intl_codes or destination in intl_codes:
                window = 10 # Extended for Intl
            
            for i in range(window):
                date_str = (now + timedelta(days=i)).strftime("%Y-%m-%d")
                tasks.append(scrape_single(origin, destination, date_str))
        
        total_tasks = len(tasks)
        
        await asyncio.gather(*tasks)
        
        # Save last success time
        async with SessionLocal() as session:
            now_iso = datetime.utcnow().isoformat()
            res = await session.execute(select(SystemSetting).where(SystemSetting.key == "last_auto_scrape"))
            setting = res.scalar_one_or_none()
            if setting:
                setting.value = now_iso
            else:
                session.add(SystemSetting(key="last_auto_scrape", value=now_iso))
            await session.commit()

        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["message"] = f"Manual Scrape Complete. Processed {completed_count} searches."
        
    except Exception as e:
        logger.error(f"Manual scrape failed: {e}")
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["message"] = str(e)

@app.post("/api/admin/run_scraper", dependencies=[Depends(verify_admin)])
async def trigger_auto_scraper(background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    # Initialize job state immediately so frontend can poll it
    JOBS[job_id] = {"status": "pending", "progress": 0, "message": "Queued"}
    background_tasks.add_task(run_manual_scrape_task, job_id)
    return {"status": "started", "job_id": job_id}

@app.get("/api/admin/cache_stats", dependencies=[Depends(verify_admin)])
async def get_cache_stats(db: AsyncSession = Depends(get_db)):
    # Count total rows in FlightCache
    res = await db.execute(select(func.count()).select_from(FlightCache))
    count = res.scalar_one()
    return {"total_flights": count}

@app.get("/api/admin/cached_routes", dependencies=[Depends(verify_admin)])
async def get_cached_routes(db: AsyncSession = Depends(get_db)):
    # Fetch all cache entries (lightweight, maybe select specific columns if data is huge)
    # Ideally we just need metadata, but data is in JSON.
    # Optimization: If 'data' is large, this might be slow. But for admin view it's okay for now.
    stmt = select(FlightCache)
    res = await db.execute(stmt)
    entries = res.scalars().all()
    
    # Structure: Date -> Origin -> Destination -> Flight Count
    grouped = {}
    
    for e in entries:
        date = e.travel_date
        if date not in grouped: grouped[date] = {}
        
        if e.origin not in grouped[date]: grouped[date][e.origin] = {}
        
        # Count actual flights in the JSON blob
        flights = decompress_data(e.data)
        flight_count = len(flights) if flights else 0
        
        # Ensure UTC for frontend conversion
        ts = e.created_at
        if ts and ts.tzinfo is None:
            ts = pytz.UTC.localize(ts)

        # Return enriched object
        grouped[date][e.origin][e.destination] = {
            "count": flight_count,
            "updated": ts.isoformat() if ts else None
        }
        
    return grouped

@app.delete("/api/admin/cached_routes/{date_str}", dependencies=[Depends(verify_admin)])
async def delete_flight_cache_date(date_str: str, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(FlightCache).where(FlightCache.travel_date == date_str))
    await db.commit()
    return {"status": "deleted"}

@app.get("/api/admin/weather_data", dependencies=[Depends(verify_admin)])
async def get_weather_data_admin(db: AsyncSession = Depends(get_db)):
    stmt = select(WeatherData)
    res = await db.execute(stmt)
    data = res.scalars().all()
    
    grouped = {}
    for d in data:
        date = d.date
        if date not in grouped: grouped[date] = []
        
        ts = d.updated_at
        if ts and ts.tzinfo is None:
            ts = pytz.UTC.localize(ts)

        grouped[date].append({
            "airport": d.airport_code,
            "temp": d.temp_high,
            "condition": d.condition_code,
            "updated": ts.isoformat() if ts else None
        })
    
    return grouped

@app.delete("/api/admin/weather_data/{date_str}", dependencies=[Depends(verify_admin)])
async def delete_weather_data_date(date_str: str, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(WeatherData).where(WeatherData.date == date_str))
    await db.commit()
    return {"status": "deleted"}