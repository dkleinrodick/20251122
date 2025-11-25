import asyncio
import httpx
import json
from app.scraper import FrontierClient

async def test_routes():
    print("--- Route Validation Debug ---")
    client = FrontierClient(timeout=30.0)
    
    scenarios = [
        ("Invalid", "MDW", "ORD", "2025-12-01"), # Likely invalid
        ("Valid_Active", "DEN", "MCO", "2025-12-01"), # Definitely active
        ("Valid_Maybe_Empty", "ORD", "PSP", "2025-11-28") # Your example
    ]
    
    await client.authenticate()
    
    for name, origin, dest, date in scenarios:
        print(f"\nTesting {name}: {origin}->{dest} on {date}")
        try:
            data = await client.search(origin, dest, date)
            # If successful (200 OK), check results
            trips = data.get('data', {}).get('results', [])
            if trips:
                print(f"  [SUCCESS] Found {len(trips)} trip options. Route VALID.")
            else:
                print(f"  [SUCCESS] 200 OK but NO TRIPS. Route VALID but EMPTY/SOLD OUT.")
                
        except Exception as e:
            # Check if it's an HTTP error
            if hasattr(e, 'response'):
                print(f"  [ERROR] HTTP {e.response.status_code}")
                try:
                    print(f"  Body: {e.response.text[:200]}...")
                except: pass
            else:
                print(f"  [ERROR] Exception: {e}")

    await client.close()

if __name__ == "__main__":
    asyncio.run(test_routes())
