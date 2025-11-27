"""
Verify that paired date ranges consistently return both dates
Test with different routes and date pairs
"""
import asyncio
from datetime import datetime, timedelta
from app.scraper import FrontierClient

async def verify_paired_strategy():
    """Verify the paired date strategy works reliably"""
    print("=" * 80)
    print("VERIFICATION: Paired Date Strategy")
    print("=" * 80)

    # Test different routes
    routes = [
        ("DEN", "MCO"),
        ("ATL", "LAS"),
        ("DFW", "PHX"),
        ("MCO", "DEN"),  # Reverse direction
    ]

    base_date = datetime.now().date() + timedelta(days=1)

    # Test 11 pairs (covering 21 days + 1 single day)
    pairs = []
    for i in range(10):  # 10 pairs = 20 days
        start = base_date + timedelta(days=i*2)
        end = start + timedelta(days=1)
        pairs.append((start, end, 2))  # expect 2 dates

    # Last day alone
    pairs.append((base_date + timedelta(days=20), base_date + timedelta(days=20), 1))

    total_tests = 0
    passed_tests = 0
    failed_tests = 0

    for origin, dest in routes:
        print(f"\n{'='*80}")
        print(f"Testing Route: {origin} -> {dest}")
        print(f"{'='*80}")

        client = FrontierClient()
        await client.authenticate()

        for idx, (start, end, expected) in enumerate(pairs[:3], 1):  # Test first 3 pairs per route
            total_tests += 1

            payload = {
                "flightAvailabilityRequestModel": {
                    "passengers": {"types": [{"type": "ADT", "count": 1}], "residentCountry": "US"},
                    "filters": {"maxConnections": 20, "fareInclusionType": "Default", "type": "All"},
                    "codes": {"currencyCode": "USD"},
                    "origin": origin,
                    "destination": dest,
                    "beginDate": str(start),
                    "endDate": str(end)
                }
            }

            try:
                resp = await client.client.post(
                    f"{client.BASE_URL}/flightavailabilityssv/FlightAvailabilitySimpleSearch",
                    json=payload,
                    headers=client.headers
                )

                if resp.status_code == 200:
                    data = resp.json()
                    dates = extract_dates(data)

                    if len(dates) == expected:
                        passed_tests += 1
                        print(f"  Pair {idx}: {start} to {end} -> [OK] Got {len(dates)} dates as expected")
                    else:
                        failed_tests += 1
                        print(f"  Pair {idx}: {start} to {end} -> [FAIL] Got {len(dates)} dates, expected {expected}")
                        print(f"    Dates: {sorted(dates)}")
                else:
                    failed_tests += 1
                    print(f"  Pair {idx}: {start} to {end} -> [FAIL] HTTP {resp.status_code}")

            except Exception as e:
                failed_tests += 1
                print(f"  Pair {idx}: {start} to {end} -> [ERROR] {type(e).__name__}: {str(e)[:50]}")

            await asyncio.sleep(0.3)

        await client.close()
        await asyncio.sleep(1)

    print("\n" + "=" * 80)
    print("VERIFICATION RESULTS")
    print("=" * 80)
    print(f"Total Tests: {total_tests}")
    print(f"Passed: {passed_tests} ({round(100*passed_tests/total_tests, 1)}%)")
    print(f"Failed: {failed_tests}")

    if passed_tests == total_tests:
        print("\n[SUCCESS] Paired date strategy is RELIABLE!")
        print("\nRecommended Implementation:")
        print("  - Group 21 dates into 11 requests (10 pairs + 1 single)")
        print("  - Reduces API calls from 17,178 to 8,998 (48% reduction)")
        print("  - Save both dates from each paired response")
    elif passed_tests / total_tests >= 0.9:
        print("\n[MOSTLY RELIABLE] Strategy works 90%+ of the time")
        print("  Consider implementing with error handling for edge cases")
    else:
        print("\n[UNRELIABLE] Too many failures, don't use this strategy")

    return passed_tests == total_tests

def extract_dates(data):
    """Helper to extract all unique dates from response"""
    dates = set()
    try:
        results = data.get('data', {}).get('results', [])
        for result in results:
            for trip in result.get('trips', []):
                journeys_dict = trip.get('journeysAvailableByMarket', {})
                if not isinstance(journeys_dict, dict):
                    continue
                for _, journeys in journeys_dict.items():
                    for journey in journeys:
                        dept = journey.get('designator', {}).get('departure', '')
                        if dept:
                            dates.add(dept[:10])
    except:
        pass
    return dates

async def test_data_completeness():
    """Verify that paired requests return complete data for both dates"""
    print("\n" + "=" * 80)
    print("DATA COMPLETENESS TEST")
    print("=" * 80)
    print("Comparing paired request vs individual requests")

    client1 = FrontierClient()
    client2 = FrontierClient()
    client3 = FrontierClient()

    await client1.authenticate()
    await client2.authenticate()
    await client3.authenticate()

    base_date = datetime.now().date() + timedelta(days=7)
    date1 = str(base_date)
    date2 = str(base_date + timedelta(days=1))

    # Get paired request
    print(f"\nPaired request: {date1} to {date2}")
    payload_paired = {
        "flightAvailabilityRequestModel": {
            "passengers": {"types": [{"type": "ADT", "count": 1}], "residentCountry": "US"},
            "filters": {"maxConnections": 20, "fareInclusionType": "Default", "type": "All"},
            "codes": {"currencyCode": "USD"},
            "origin": "DEN",
            "destination": "MCO",
            "beginDate": date1,
            "endDate": date2
        }
    }

    resp_paired = await client1.client.post(
        f"{client1.BASE_URL}/flightavailabilityssv/FlightAvailabilitySimpleSearch",
        json=payload_paired,
        headers=client1.headers
    )

    # Get individual requests
    print(f"Individual request 1: {date1}")
    payload1 = {
        "flightAvailabilityRequestModel": {
            "passengers": {"types": [{"type": "ADT", "count": 1}], "residentCountry": "US"},
            "filters": {"maxConnections": 20, "fareInclusionType": "Default", "type": "All"},
            "codes": {"currencyCode": "USD"},
            "origin": "DEN",
            "destination": "MCO",
            "beginDate": date1
        }
    }

    resp1 = await client2.client.post(
        f"{client2.BASE_URL}/flightavailabilityssv/FlightAvailabilitySimpleSearch",
        json=payload1,
        headers=client2.headers
    )

    print(f"Individual request 2: {date2}")
    payload2 = {
        "flightAvailabilityRequestModel": {
            "passengers": {"types": [{"type": "ADT", "count": 1}], "residentCountry": "US"},
            "filters": {"maxConnections": 20, "fareInclusionType": "Default", "type": "All"},
            "codes": {"currencyCode": "USD"},
            "origin": "DEN",
            "destination": "MCO",
            "beginDate": date2
        }
    }

    resp2 = await client3.client.post(
        f"{client3.BASE_URL}/flightavailabilityssv/FlightAvailabilitySimpleSearch",
        json=payload2,
        headers=client3.headers
    )

    # Compare
    if all([resp_paired.status_code == 200, resp1.status_code == 200, resp2.status_code == 200]):
        data_paired = resp_paired.json()
        data1 = resp1.json()
        data2 = resp2.json()

        # Count flights for each date
        def count_flights_by_date(data):
            counts = {}
            results = data.get('data', {}).get('results', [])
            for result in results:
                for trip in result.get('trips', []):
                    journeys_dict = trip.get('journeysAvailableByMarket', {})
                    if not isinstance(journeys_dict, dict):
                        continue
                    for _, journeys in journeys_dict.items():
                        for journey in journeys:
                            dept = journey.get('designator', {}).get('departure', '')[:10]
                            counts[dept] = counts.get(dept, 0) + 1
            return counts

        paired_counts = count_flights_by_date(data_paired)
        individual1_count = count_flights_by_date(data1).get(date1, 0)
        individual2_count = count_flights_by_date(data2).get(date2, 0)

        print(f"\n--- Results ---")
        print(f"Paired request:")
        for date, count in sorted(paired_counts.items()):
            print(f"  {date}: {count} flights")

        print(f"\nIndividual requests:")
        print(f"  {date1}: {individual1_count} flights")
        print(f"  {date2}: {individual2_count} flights")

        print(f"\n--- Comparison ---")
        date1_match = paired_counts.get(date1, 0) == individual1_count
        date2_match = paired_counts.get(date2, 0) == individual2_count

        print(f"Date 1 match: {'[OK]' if date1_match else '[DIFFERENT]'}")
        print(f"Date 2 match: {'[OK]' if date2_match else '[DIFFERENT]'}")

        if date1_match and date2_match:
            print("\n[SUCCESS] Paired request returns identical data!")
        else:
            print("\n[WARNING] Data differs between paired and individual requests")

    await client1.close()
    await client2.close()
    await client3.close()

if __name__ == "__main__":
    asyncio.run(verify_paired_strategy())
    asyncio.run(test_data_completeness())
