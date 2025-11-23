import uvicorn
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from datetime import datetime, timedelta
import random

# Setup
app = FastAPI(title="WildFares Mock")
templates = Jinja2Templates(directory="app/templates")

# Mock Data: Airports
MOCK_AIRPORTS = [
    {"code": "DEN", "city": "Denver", "name": "Denver International", "timezone": "America/Denver"},
    {"code": "LAS", "city": "Las Vegas", "name": "Harry Reid Intl", "timezone": "America/Los_Angeles"},
    {"code": "MCO", "city": "Orlando", "name": "Orlando International", "timezone": "America/New_York"},
    {"code": "ORD", "city": "Chicago", "name": "O'Hare International", "timezone": "America/Chicago"},
    {"code": "ATL", "city": "Atlanta", "name": "Hartsfield-Jackson", "timezone": "America/New_York"},
    {"code": "PHL", "city": "Philadelphia", "name": "Philadelphia Intl", "timezone": "America/New_York"},
    {"code": "SFO", "city": "San Francisco", "name": "San Francisco Intl", "timezone": "America/Los_Angeles"},
    {"code": "MIA", "city": "Miami", "name": "Miami International", "timezone": "America/New_York"},
    {"code": "LGA", "city": "New York", "name": "LaGuardia", "timezone": "America/New_York"},
    {"code": "AUS", "city": "Austin", "name": "Austin-Bergstrom", "timezone": "America/Chicago"},
    {"code": "CUN", "city": "Cancun", "name": "Cancun International", "timezone": "America/Cancun"},
]

# Mock Data: Routes (All-to-All for simplicity in mock)
MOCK_ROUTES = {
    a["code"]: [b["code"] for b in MOCK_AIRPORTS if b["code"] != a["code"]]
    for a in MOCK_AIRPORTS
}

# Helper to generate flight
def gen_flight(origin, dest, date, time_str, duration_mins, price, stops=0, via=None, seats=9):
    # date is YYYY-MM-DD, time_str is HH:MM
    dept_iso = f"{date}T{time_str}:00"
    
    # Calculate arrival
    dt = datetime.fromisoformat(dept_iso)
    arr = dt + timedelta(minutes=duration_mins)
    arr_iso = arr.isoformat()
    
    hours = duration_mins // 60
    mins = duration_mins % 60
    dur_str = f"{hours}h {mins}m"
    
    segments = []
    if stops == 0:
        segments.append({
            "origin": origin, "destination": dest, 
            "departure": dept_iso, "arrival": arr_iso, 
            "flightNumber": f"F9{random.randint(100,999)}",
            "duration": dur_str
        })
    else:
        # Split into 2 segments
        mid_dur = duration_mins // 2 - 45 # subtract layover
        mid_arr = dt + timedelta(minutes=mid_dur)
        mid_dept = mid_arr + timedelta(minutes=90) # 1.5h layover
        
        segments.append({
            "origin": origin, "destination": via, 
            "departure": dept_iso, "arrival": mid_arr.isoformat(), 
            "flightNumber": f"F9{random.randint(100,999)}",
            "duration": f"{mid_dur//60}h {mid_dur%60}m"
        })
        segments.append({
            "origin": via, "destination": dest, 
            "departure": mid_dept.isoformat(), "arrival": arr_iso, 
            "flightNumber": f"F9{random.randint(100,999)}",
            "duration": f"{mid_dur//60}h {mid_dur%60}m"
        })

    return {
        "origin": origin, "destination": dest, 
        "price": price, "total_price": price,
        "departure": dept_iso, "arrival": arr_iso,
        "duration": dur_str, "stops": stops, "seats_available": seats,
        "segments": segments,
        "layover_airports": [via] if via else [],
        "via": via,
        "total_duration_hours": duration_mins / 60,
        "total_stops": stops,
        "layover_hours": 1.5 if stops > 0 else 0
    }

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/features", response_class=HTMLResponse)
async def read_features(request: Request):
    return templates.TemplateResponse("marketing.html", {"request": request})

@app.get("/api/locations")
async def get_locations():
    return MOCK_AIRPORTS

@app.get("/api/routes")
async def get_routes():
    return MOCK_ROUTES

@app.get("/api/public_config")
async def get_config():
    return {"announcement_enabled": "true", "announcement_html": "<h3>Mock Data Loaded</h3><p>Testing environment active with expanded dataset.</p>"}

@app.get("/api/search")
async def search(origin: str, destination: str, date: str):
    # Generate dynamic results based on query
    results = []
    
    # 1. Morning Direct
    results.append(gen_flight(origin, destination, date, "06:00", 145, 19, 0, seats=9))
    
    # 2. Mid-day Direct (Past flight check: if testing today at >08:00, this might show/hide)
    results.append(gen_flight(origin, destination, date, "11:30", 150, 29, 0, seats=4))
    
    # 3. Afternoon Connection
    results.append(gen_flight(origin, destination, date, "14:00", 340, 49, 1, via="DEN" if origin!="DEN" else "MCO", seats=2))
    
    # 4. Evening Direct
    results.append(gen_flight(origin, destination, date, "19:45", 140, 19, 0, seats=9))
    
    # 5. Sold Out / Limited Seats (Test passenger filter)
    results.append(gen_flight(origin, destination, date, "21:00", 145, 15, 0, seats=1)) # Only 1 seat!

    return results

@app.get("/api/roundtrip")
async def roundtrip(origin: str, date: str):
    # Day Trip Mock
    # Return trips to 3 random destinations
    dests = [d["code"] for d in MOCK_AIRPORTS if d["code"] != origin][:3]
    results = []
    
    for d in dests:
        # Option 1: Morning (Standard)
        out_f = gen_flight(origin, d, date, "06:00", 120, 29, 0)
        ret_f = gen_flight(d, origin, date, "20:00", 120, 29, 0)
        
        results.append({
            "outbound": out_f,
            "return": ret_f,
            "destination": d,
            "total_price": out_f["price"] + ret_f["price"],
            "hours_at_dest": 12,
            "seats_available": 9
        })

        # Option 2: Afternoon/Evening (For late day testing)
        out_f2 = gen_flight(origin, d, date, "16:00", 120, 39, 0)
        ret_f2 = gen_flight(d, origin, date, "23:00", 120, 39, 0)
        
        results.append({
            "outbound": out_f2,
            "return": ret_f2,
            "destination": d,
            "total_price": out_f2["price"] + ret_f2["price"],
            "hours_at_dest": 5,
            "seats_available": 4
        })
        
    return results

@app.get("/api/builder")
async def builder(origin: str, destination: str, date: str):
    # Return complex multi-hop mock
    return [
        gen_flight(origin, destination, date, "08:00", 400, 59, 1, via="LAS" if origin!="LAS" else "DEN"),
        gen_flight(origin, destination, date, "10:00", 500, 89, 1, via="MCO" if origin!="MCO" else "PHL"),
    ]

@app.get("/api/map_data")
async def map_data(date: str):
    features = []
    for a in MOCK_AIRPORTS:
        # Mock coordinates (approx)
        coords = {
            "DEN": [-104.67, 39.85], "LAS": [-115.15, 36.08], "MCO": [-81.30, 28.43],
            "ORD": [-87.90, 41.97], "ATL": [-84.42, 33.64], "PHL": [-75.24, 39.87],
            "SFO": [-122.37, 37.62], "MIA": [-80.28, 25.79], "LGA": [-73.87, 40.77],
            "AUS": [-97.67, 30.19], "CUN": [-86.87, 21.04]
        }
        c = coords.get(a["code"], [0, 0])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": c},
            "properties": {
                "code": a["code"], "city": a["city"], 
                "temp": random.randint(30, 90), "condition": random.choice([0, 1, 2, 3, 51, 61, 80])
            }
        })
    return {"type": "FeatureCollection", "features": features}

@app.get("/api/explore_advanced")
async def explore(date: str):
    # Return many flights from random origins to random dests
    res = []
    for _ in range(20):
        o = random.choice(MOCK_AIRPORTS)["code"]
        d = random.choice(MOCK_AIRPORTS)["code"]
        if o == d: continue
        res.append(gen_flight(o, d, date, "10:00", 120, 19))
    return res

if __name__ == "__main__":
    print("Starting Mock Server on http://localhost:8003")
    uvicorn.run(app, host="127.0.0.1", port=8003)