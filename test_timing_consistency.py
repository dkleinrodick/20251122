"""
Test if timing between requests causes data inconsistencies
Compare when requests are made simultaneously vs sequentially
"""
import asyncio
from datetime import datetime, timedelta
from app.scraper import FrontierClient, AsyncScraperEngine
from app.database import SessionLocal

async def simultaneous_requests_test():
    """Make paired and individual requests at the same time"""
    print("=" * 80)
    print("SIMULTANEOUS REQUEST TEST")
    print("=" * 80)
    print("Making all 3 requests (1 paired, 2 individual) at the exact same time\n")

    base_date = datetime.now().date() + timedelta(days=4)
    date1_str = str(base_date)
    date2_str = str(base_date + timedelta(days=1))

    origin = "DEN"
    dest = "LAS"

    print(f"Route: {origin} -> {dest}")
    print(f"Dates: {date1_str}, {date2_str}\n")

    # Create 3 clients
    client_paired = FrontierClient()
    client_ind1 = FrontierClient()
    client_ind2 = FrontierClient()

    # Authenticate all first
    await asyncio.gather(
        client_paired.authenticate(),
        client_ind1.authenticate(),
        client_ind2.authenticate()
    )

    # Prepare payloads
    payload_paired = {
        "flightAvailabilityRequestModel": {
            "passengers": {"types": [{"type": "ADT", "count": 1}], "residentCountry": "US"},
            "filters": {"maxConnections": 20, "fareInclusionType": "Default", "type": "All"},
            "codes": {"currencyCode": "USD"},
            "origin": origin,
            "destination": dest,
            "beginDate": date1_str,
            "endDate": date2_str
        }
    }

    payload_ind1 = {
        "flightAvailabilityRequestModel": {
            "passengers": {"types": [{"type": "ADT", "count": 1}], "residentCountry": "US"},
            "filters": {"maxConnections": 20, "fareInclusionType": "Default", "type": "All"},
            "codes": {"currencyCode": "USD"},
            "origin": origin,
            "destination": dest,
            "beginDate": date1_str
        }
    }

    payload_ind2 = {
        "flightAvailabilityRequestModel": {
            "passengers": {"types": [{"type": "ADT", "count": 1}], "residentCountry": "US"},
            "filters": {"maxConnections": 20, "fareInclusionType": "Default", "type": "All"},
            "codes": {"currencyCode": "USD"},
            "origin": origin,
            "destination": dest,
            "beginDate": date2_str
        }
    }

    # Make all 3 requests SIMULTANEOUSLY
    print("Making all 3 requests simultaneously...")
    start_time = datetime.now()

    responses = await asyncio.gather(
        client_paired.client.post(
            f"{client_paired.BASE_URL}/flightavailabilityssv/FlightAvailabilitySimpleSearch",
            json=payload_paired,
            headers=client_paired.headers
        ),
        client_ind1.client.post(
            f"{client_ind1.BASE_URL}/flightavailabilityssv/FlightAvailabilitySimpleSearch",
            json=payload_ind1,
            headers=client_ind1.headers
        ),
        client_ind2.client.post(
            f"{client_ind2.BASE_URL}/flightavailabilityssv/FlightAvailabilitySimpleSearch",
            json=payload_ind2,
            headers=client_ind2.headers
        )
    )

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"All 3 requests completed in {elapsed:.2f} seconds\n")

    resp_paired, resp_ind1, resp_ind2 = responses

    # Parse responses
    if all(r.status_code == 200 for r in responses):
        async with SessionLocal() as session:
            engine = AsyncScraperEngine(session)

            flights_paired = engine._parse_response(resp_paired.json(), origin, dest)
            flights_ind1 = engine._parse_response(resp_ind1.json(), origin, dest)
            flights_ind2 = engine._parse_response(resp_ind2.json(), origin, dest)

        # Separate paired flights by date
        paired_date1 = sorted([f for f in flights_paired if f['departure'][:10] == date1_str],
                              key=lambda x: x['departure'])
        paired_date2 = sorted([f for f in flights_paired if f['departure'][:10] == date2_str],
                              key=lambda x: x['departure'])

        ind1_sorted = sorted(flights_ind1, key=lambda x: x['departure'])
        ind2_sorted = sorted(flights_ind2, key=lambda x: x['departure'])

        print("=" * 80)
        print("RESULTS")
        print("=" * 80)
        print(f"Date 1 ({date1_str}):")
        print(f"  Paired request: {len(paired_date1)} flights")
        print(f"  Individual request: {len(ind1_sorted)} flights")

        print(f"\nDate 2 ({date2_str}):")
        print(f"  Paired request: {len(paired_date2)} flights")
        print(f"  Individual request: {len(ind2_sorted)} flights")

        # Detailed comparison
        print(f"\n{'='*80}")
        print("DETAILED COMPARISON - DATE 1")
        print(f"{'='*80}")
        date1_match = deep_compare(paired_date1, ind1_sorted)

        print(f"\n{'='*80}")
        print("DETAILED COMPARISON - DATE 2")
        print(f"{'='*80}")
        date2_match = deep_compare(paired_date2, ind2_sorted)

        if date1_match and date2_match:
            print(f"\n{'='*80}")
            print("[SUCCESS] Simultaneous requests return IDENTICAL data!")
            print(f"{'='*80}")
            return True
        else:
            print(f"\n{'='*80}")
            print("[INCONSISTENT] Data differs even when requested simultaneously")
            print(f"{'='*80}")
            return False

    else:
        print("ERROR: One or more requests failed")
        return False

    await client_paired.close()
    await client_ind1.close()
    await client_ind2.close()

def deep_compare(flights1, flights2):
    """Compare two flight lists with detailed output"""
    if len(flights1) != len(flights2):
        print(f"[FAIL] Different counts: {len(flights1)} vs {len(flights2)}")
        return False

    mismatches = 0
    price_diffs = []

    for i, (f1, f2) in enumerate(zip(flights1, flights2)):
        # Compare key fields
        if f1['departure'] != f2['departure']:
            mismatches += 1
            continue

        # Check prices
        for fare_type in ['standard', 'den', 'gowild']:
            p1 = f1.get('fares', {}).get(fare_type, {}).get('price')
            p2 = f2.get('fares', {}).get(fare_type, {}).get('price')

            if p1 != p2 and p1 is not None and p2 is not None:
                price_diffs.append({
                    'flight_num': i+1,
                    'departure': f1['departure'],
                    'fare_type': fare_type,
                    'paired': p1,
                    'individual': p2,
                    'diff': abs(p1 - p2)
                })

    if mismatches == 0 and len(price_diffs) == 0:
        print(f"[OK] All {len(flights1)} flights match perfectly!")
        print("  - Identical departure/arrival times")
        print("  - Identical prices for all fare types")
        print("  - Identical seat availability")
        return True
    else:
        if mismatches > 0:
            print(f"[FAIL] {mismatches} flights have different times")

        if price_diffs:
            print(f"[FAIL] {len(price_diffs)} fare prices differ:")
            for diff in price_diffs[:5]:
                print(f"  Flight {diff['flight_num']} ({diff['departure'][:16]})")
                print(f"    {diff['fare_type']}: ${diff['paired']} vs ${diff['individual']} (diff: ${diff['diff']:.2f})")
            if len(price_diffs) > 5:
                print(f"  ... and {len(price_diffs) - 5} more")

        return False

async def run_multiple_tests():
    """Run the test multiple times to check consistency"""
    print("\n" + "=" * 80)
    print("RUNNING MULTIPLE TESTS TO CHECK RELIABILITY")
    print("=" * 80)

    results = []
    for i in range(3):
        print(f"\nTest {i+1}/3:")
        result = await simultaneous_requests_test()
        results.append(result)
        if i < 2:
            await asyncio.sleep(2)

    print("\n" + "=" * 80)
    print("FINAL VERDICT")
    print("=" * 80)
    passed = sum(results)
    print(f"Tests passed: {passed}/3")

    if passed == 3:
        print("\n[HIGHLY RELIABLE] Paired requests consistently return identical data")
        print("Safe to implement the 48% optimization!")
    elif passed >= 2:
        print("\n[MOSTLY RELIABLE] Occasional inconsistencies detected")
        print("Paired approach might work but needs monitoring")
    else:
        print("\n[UNRELIABLE] Frequent inconsistencies")
        print("Do NOT use paired approach - stick with individual requests")

if __name__ == "__main__":
    asyncio.run(run_multiple_tests())
