from sqlalchemy.future import select
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta
import json
import re

from app.models import FlightCache, Airport, WeatherData, RoutePair
from app.airports_data import AIRPORTS_LIST
from app.compression import decompress_data

def parse_iso_date(date_str: str) -> datetime:
    """Fast ISO date parsing helper."""
    if not date_str: return datetime.min
    try:
        # Removing 'Z' is faster than full isoformat with timezone sometimes, but 
        # strictly we should handle it. Assuming 'Z' means UTC or naive local.
        # The scraper output typically has 'Z' but might be local time effectively.
        return datetime.fromisoformat(date_str.replace('Z', ''))
    except (ValueError, TypeError):
        return datetime.min

async def find_round_trip_same_day(session: AsyncSession, origin: str, dest: str, date: str, min_hours: int = 4):
    # 1. Get Outbound
    stmt_out = select(FlightCache).where(
        FlightCache.origin == origin,
        FlightCache.travel_date == date
    )
    if dest:
        stmt_out = stmt_out.where(FlightCache.destination == dest)
        
    res_out = await session.execute(stmt_out)
    outbound_caches = res_out.scalars().all()
    
    if not outbound_caches: return []

    # Gather potential destinations
    potential_dests = [c.destination for c in outbound_caches]
    
    # 2. Get Inbound (from potential dests back to origin)
    stmt_in = select(FlightCache).where(
        FlightCache.origin.in_(potential_dests),
        FlightCache.destination == origin,
        FlightCache.travel_date == date
    )
    res_in = await session.execute(stmt_in)
    inbound_caches = res_in.scalars().all()
    
    # Optimize: Pre-process inbound flights with parsed datetimes
    inbound_map = {}
    for c in inbound_caches:
        if c.origin not in inbound_map:
            inbound_map[c.origin] = []
        
        flights = decompress_data(c.data)
        if not flights: continue
        
        for f in flights:
            dept_dt = parse_iso_date(f.get('departure'))
            if dept_dt != datetime.min:
                # Store tuple (flight_obj, parsed_datetime)
                inbound_map[c.origin].append((f, dept_dt))
    
    pairs = []
    min_seconds = min_hours * 3600
    
    for out_cache in outbound_caches:
        destination = out_cache.destination
        out_flights = decompress_data(out_cache.data)
        in_flights_processed = inbound_map.get(destination, [])
        
        if not out_flights or not in_flights_processed: continue
        
        for out_f in out_flights:
            arr_dt = parse_iso_date(out_f.get('arrival'))
            if arr_dt == datetime.min: continue
                
            for in_f, dept_dt in in_flights_processed:
                # Check min hours at destination
                diff_seconds = (dept_dt - arr_dt).total_seconds()
                if diff_seconds >= min_seconds:
                    p1 = float(out_f.get('price') or 0)
                    p2 = float(in_f.get('price') or 0)
                    total_price = round(p1 + p2, 2)
                    
                    pairs.append({
                        "destination": destination,
                        "outbound": out_f,
                        "return": in_f,
                        "total_price": total_price,
                        "time_at_dest": str(dept_dt - arr_dt),
                        "hours_at_dest": round(diff_seconds / 3600, 1)
                    })
            
    # Sort by price
    pairs.sort(key=lambda x: x['total_price'])
    return pairs

def parse_duration_str(dur_str: str) -> float:
    if not dur_str: return 0.0
    # Standardize (remove +1 day etc)
    clean_str = dur_str.split('(')[0].strip()
    
    hours = 0
    minutes = 0
    
    # Extract Hours
    h_match = re.search(r'(\d+)h', clean_str)
    if h_match:
        hours = int(h_match.group(1))
        
    # Extract Minutes
    m_match = re.search(r'(\d+)m', clean_str)
    if m_match:
        minutes = int(m_match.group(1))
        
    return hours + (minutes / 60.0)

async def build_multi_hop_route(session: AsyncSession, origin: str, dest: str, date: str, min_layover_h: float, max_layover_h: float, max_duration: float = 24.0, max_stops: int = 3):
    """
    Finds Route: Origin -> Hub -> Dest AND Direct Flights
    """
    routes = []
    existing_signatures = set()

    # 0. Fetch STANDARD flights (Origin -> Dest) first to avoid duplicates
    stmt_std = select(FlightCache).where(
        FlightCache.origin == origin, 
        FlightCache.destination == dest, 
        FlightCache.travel_date == date
    )
    res_std = await session.execute(stmt_std)
    std_cache = res_std.scalars().first()
    
    if std_cache and std_cache.data:
        std_flights = decompress_data(std_cache.data)
        for f in std_flights:
            # Create signature: "F9123-F9456"
            sig = "-".join([s.get('flightNumber', '') for s in f.get('segments', [])])
            existing_signatures.add(sig)
            
            f_copy = f.copy()
            dur_str = f.get('duration', '0h')
            try:
                dur = parse_duration_str(dur_str)
            except:
                dur = 0
            
            stops = f.get('stops', 0)
            price = f.get('price') or 0
            
            if dur <= max_duration and stops <= max_stops:
                 routes.append({
                     "segments": [f_copy],
                     "via": "Standard",
                     "layover_hours": 0,
                     "total_price": price,
                     "total_duration_hours": dur,
                     "total_stops": stops,
                     "departure": f.get('departure'),
                     "arrival": f.get('arrival')
                 })

    # 1. Find all flights from Origin on Date (potential Leg 1)
    stmt = select(FlightCache).where(FlightCache.origin == origin, FlightCache.travel_date == date)
    res = await session.execute(stmt)
    outbound_caches = res.scalars().all()
    
    for cache in outbound_caches:
        hub = cache.destination
        if hub == dest: continue
        
        out_flights = decompress_data(cache.data)
        if not out_flights: continue

        # Pre-parse outbound flights
        parsed_out = []
        for f1 in out_flights:
            arr_dt = parse_iso_date(f1.get('arrival'))
            if arr_dt != datetime.min:
                parsed_out.append((f1, arr_dt))
        
        if not parsed_out: continue

        # 2. For this Hub, find flights to Dest
        # Check Same Day
        stmt2 = select(FlightCache).where(
            FlightCache.origin == hub, 
            FlightCache.destination == dest, 
            FlightCache.travel_date == date
        )
        res2 = await session.execute(stmt2)
        in_cache_same = res2.scalars().first()
        
        # Check Next Day (for overnight layovers)
        next_day = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        stmt3 = select(FlightCache).where(
            FlightCache.origin == hub, 
            FlightCache.destination == dest, 
            FlightCache.travel_date == next_day
        )
        res3 = await session.execute(stmt3)
        in_cache_next = res3.scalars().first()
        
        potential_next_flights = []
        if in_cache_same and in_cache_same.data: potential_next_flights.extend(decompress_data(in_cache_same.data))
        if in_cache_next and in_cache_next.data: potential_next_flights.extend(decompress_data(in_cache_next.data))
        
        if not potential_next_flights: continue
        
        # Pre-parse inbound/next leg flights
        parsed_next = []
        for f2 in potential_next_flights:
            dept_dt = parse_iso_date(f2.get('departure'))
            if dept_dt != datetime.min:
                parsed_next.append((f2, dept_dt))

        # 3. Match timings
        for f1, arr1_dt in parsed_out:
            for f2, dept2_dt in parsed_next:
                diff = (dept2_dt - arr1_dt).total_seconds() / 3600
                
                if min_layover_h <= diff <= max_layover_h:
                    # Check signature
                    f1_nums = [s.get('flightNumber') for s in f1.get('segments', [])]
                    f2_nums = [s.get('flightNumber') for s in f2.get('segments', [])]
                    constructed_sig = "-".join(f1_nums + f2_nums)
                    
                    if constructed_sig in existing_signatures:
                        continue 

                    total_price = (f1.get('price') or 0) + (f2.get('price') or 0)
                    
                    # Calculate total duration
                    dep_origin = parse_iso_date(f1.get('departure'))
                    arr_final = parse_iso_date(f2.get('arrival'))
                    total_dur = round((arr_final - dep_origin).total_seconds() / 3600, 1)
                    
                    # Calculate total stops
                    stops1 = f1.get('stops', 0)
                    stops2 = f2.get('stops', 0)
                    total_stops = stops1 + stops2 + 1

                    if total_dur <= max_duration and total_stops <= max_stops:
                        routes.append({
                            "segments": [f1, f2],
                            "via": hub,
                            "layover_hours": round(diff, 1),
                            "total_price": total_price,
                            "total_duration_hours": total_dur,
                            "total_stops": total_stops,
                            "departure": f1.get('departure'), 
                            "arrival": f2.get('arrival')
                        })

    routes.sort(key=lambda x: x.get('total_price', 0))
    return routes

async def get_map_data(session: AsyncSession, date: str):
    # Get all airports with coordinates
    airports_res = await session.execute(select(Airport))
    airports = airports_res.scalars().all()
    
    # Get weather for date
    weather_res = await session.execute(select(WeatherData).where(WeatherData.date == date))
    weather_map = {w.airport_code: {"temp": w.temp_high, "code": w.condition_code} for w in weather_res.scalars().all()}
    
    features = []
    for ap in airports:
        if not ap.latitude or not ap.longitude: continue
        
        w = weather_map.get(ap.code, {})
        
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [ap.longitude, ap.latitude]
            },
            "properties": {
                "code": ap.code,
                "city": ap.city_name,
                "temp": w.get("temp", "N/A"),
                "condition": w.get("code", "N/A")
            }
        })
        
    return {
        "type": "FeatureCollection",
        "features": features
    }