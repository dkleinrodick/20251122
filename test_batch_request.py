"""
Test script to check if Frontier API supports batch requests
(multiple origins, destinations, or dates in one call)
"""
import asyncio
import json
from app.scraper import FrontierClient

async def test_single_request():
    """Baseline: Normal single request"""
    print("=" * 60)
    print("TEST 1: Single Request (Baseline)")
    print("=" * 60)

    client = FrontierClient()
    try:
        result = await client.search("DEN", "MCO", "2025-12-01")
        print(f"[OK] Single request successful")
        print(f"  Response keys: {list(result.keys())}")
        if 'data' in result:
            print(f"  Results found: {len(result.get('data', {}).get('results', []))}")
    except Exception as e:
        print(f"[FAIL] Failed: {e}")
    finally:
        await client.close()

async def test_multiple_dates():
    """Test if we can send multiple dates in beginDate field"""
    print("\n" + "=" * 60)
    print("TEST 2: Multiple Dates in Array")
    print("=" * 60)

    client = FrontierClient()
    await client.authenticate()

    # Try sending dates as array
    payload = {
        "flightAvailabilityRequestModel": {
            "passengers": {"types": [{"type": "ADT", "count": 1}], "residentCountry": "US"},
            "filters": {"maxConnections": 20, "fareInclusionType": "Default", "type": "All"},
            "codes": {"currencyCode": "USD"},
            "origin": "DEN",
            "destination": "MCO",
            "beginDate": ["2025-12-01", "2025-12-02", "2025-12-03"]  # Array of dates
        }
    }

    try:
        resp = await client.client.post(
            f"{client.BASE_URL}/flightavailabilityssv/FlightAvailabilitySimpleSearch",
            json=payload,
            headers=client.headers
        )
        print(f"  Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"[OK] Multiple dates accepted!")
            print(f"  Response: {json.dumps(data, indent=2)[:500]}...")
        else:
            print(f"[FAIL] Failed with status {resp.status_code}")
            print(f"  Response: {resp.text[:500]}")
    except Exception as e:
        print(f"[FAIL] Exception: {e}")
    finally:
        await client.close()

async def test_date_range():
    """Test if we can send a date range (endDate field)"""
    print("\n" + "=" * 60)
    print("TEST 3: Date Range with endDate")
    print("=" * 60)

    client = FrontierClient()
    await client.authenticate()

    payload = {
        "flightAvailabilityRequestModel": {
            "passengers": {"types": [{"type": "ADT", "count": 1}], "residentCountry": "US"},
            "filters": {"maxConnections": 20, "fareInclusionType": "Default", "type": "All"},
            "codes": {"currencyCode": "USD"},
            "origin": "DEN",
            "destination": "MCO",
            "beginDate": "2025-12-01",
            "endDate": "2025-12-03"  # Try adding end date
        }
    }

    try:
        resp = await client.client.post(
            f"{client.BASE_URL}/flightavailabilityssv/FlightAvailabilitySimpleSearch",
            json=payload,
            headers=client.headers
        )
        print(f"  Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"[OK] Date range accepted!")
            results = data.get('data', {}).get('results', [])
            print(f"  Number of result groups: {len(results)}")
            # Check if multiple dates returned
            unique_dates = set()
            for result in results:
                for trip in result.get('trips', []):
                    for journey_market in trip.get('journeysAvailableByMarket', {}).values():
                        for journey in journey_market:
                            dept = journey.get('designator', {}).get('departure', '')
                            if dept:
                                unique_dates.add(dept[:10])
            print(f"  Unique departure dates: {sorted(unique_dates)}")
        else:
            print(f"[FAIL] Failed with status {resp.status_code}")
            print(f"  Response: {resp.text[:500]}")
    except Exception as e:
        print(f"[FAIL] Exception: {e}")
    finally:
        await client.close()

async def test_multiple_origins():
    """Test if we can send multiple origins"""
    print("\n" + "=" * 60)
    print("TEST 4: Multiple Origins in Array")
    print("=" * 60)

    client = FrontierClient()
    await client.authenticate()

    payload = {
        "flightAvailabilityRequestModel": {
            "passengers": {"types": [{"type": "ADT", "count": 1}], "residentCountry": "US"},
            "filters": {"maxConnections": 20, "fareInclusionType": "Default", "type": "All"},
            "codes": {"currencyCode": "USD"},
            "origin": ["DEN", "LAS"],  # Multiple origins
            "destination": "MCO",
            "beginDate": "2025-12-01"
        }
    }

    try:
        resp = await client.client.post(
            f"{client.BASE_URL}/flightavailabilityssv/FlightAvailabilitySimpleSearch",
            json=payload,
            headers=client.headers
        )
        print(f"  Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"[OK] Multiple origins accepted!")
            print(f"  Response: {json.dumps(data, indent=2)[:500]}...")
        else:
            print(f"[FAIL] Failed with status {resp.status_code}")
            print(f"  Response: {resp.text[:500]}")
    except Exception as e:
        print(f"[FAIL] Exception: {e}")
    finally:
        await client.close()

async def main():
    print("\nTesting Frontier API Batch Request Capabilities")
    print("=" * 60)

    await test_single_request()
    await test_multiple_dates()
    await test_date_range()
    await test_multiple_origins()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("If none of the batch methods work, the API only supports")
    print("one request per origin-destination-date combination.")
    print("This would mean 17,178 tasks = 17,178 API calls.")

if __name__ == "__main__":
    asyncio.run(main())
