"""
Test if a single auth token can be shared across multiple clients/proxies
This could reduce auth overhead from 50 auth calls to 1 auth call
"""
import asyncio
import traceback
from datetime import datetime, timedelta
from app.scraper import FrontierClient, ProxyManager
from app.database import SessionLocal

async def test_shared_token_single_proxy():
    """Test if one token works for multiple requests on same client"""
    print("=" * 80)
    print("TEST 1: Single Token, Single Client (Baseline)")
    print("=" * 80)

    client = FrontierClient()
    await client.authenticate()

    token = client.token
    print(f"Auth token: {token[:20]}...")

    # Make 5 requests with the same token
    routes = [
        ("DEN", "MCO"),
        ("ATL", "LAS"),
        ("DFW", "PHX"),
        ("MCO", "DEN"),
        ("LAS", "ATL"),
    ]

    date_str = str(datetime.now().date() + timedelta(days=7))

    success = 0
    for origin, dest in routes:
        try:
            result = await client.search(origin, dest, date_str)
            print(f"  [OK] {origin}->{dest}")
            success += 1
        except Exception as e:
            print(f"  [FAIL] {origin}->{dest}: {e}")

    await client.close()

    print(f"\nResult: {success}/5 requests succeeded with same token")
    return success == 5

async def test_shared_token_multiple_clients():
    """Test if one token works across multiple clients (different proxies)"""
    print("\n" + "=" * 80)
    print("TEST 2: Single Token, Multiple Clients with Different Proxies")
    print("=" * 80)

    # Get a single auth token
    auth_client = FrontierClient()
    await auth_client.authenticate()

    shared_token = auth_client.token
    shared_headers = auth_client.headers.copy()

    print(f"Shared auth token: {shared_token[:20]}...")

    await auth_client.close()

    # Create multiple clients with different proxies
    async with SessionLocal() as session:
        proxy_mgr = ProxyManager(session)
        await proxy_mgr.load_proxies()

        clients = []
        for i in range(5):
            proxy = await proxy_mgr.get_next_proxy()
            client = FrontierClient(proxy=proxy, timeout=20.0)

            # MANUALLY set the token and headers instead of authenticating
            client.token = shared_token
            client.headers = shared_headers.copy()

            clients.append(client)
            print(f"  Client {i+1}: Using proxy {proxy[:30] if proxy else 'None'}... with shared token")

    # Test each client
    date_str = str(datetime.now().date() + timedelta(days=7))
    routes = [
        ("DEN", "MCO"),
        ("ATL", "LAS"),
        ("DFW", "PHX"),
        ("MCO", "DEN"),
        ("LAS", "ATL"),
    ]

    print(f"\nMaking requests with {len(clients)} clients sharing 1 token...")

    success = 0
    failed = 0

    for i, (client, (origin, dest)) in enumerate(zip(clients, routes), 1):
        try:
            result = await client.search(origin, dest, date_str)
            print(f"  [OK] Client {i}: {origin}->{dest}")
            success += 1
        except Exception as e:
            print(f"  [FAIL] Client {i}: {origin}->{dest} - {type(e).__name__}: {str(e)[:60]}")
            failed += 1

    # Cleanup
    for client in clients:
        await client.close()

    print(f"\nResult: {success}/{len(clients)} requests succeeded")
    print(f"  Success: {success}, Failed: {failed}")

    return success == len(clients)

async def test_concurrent_shared_token():
    """Test if shared token works with concurrent requests"""
    print("\n" + "=" * 80)
    print("TEST 3: Single Token, Concurrent Requests (20 simultaneous)")
    print("=" * 80)

    # Get a single auth token
    auth_client = FrontierClient()
    await auth_client.authenticate()

    shared_token = auth_client.token
    shared_headers = auth_client.headers.copy()

    print(f"Shared auth token: {shared_token[:20]}...")

    await auth_client.close()

    # Create pool of clients sharing the token
    async with SessionLocal() as session:
        proxy_mgr = ProxyManager(session)
        await proxy_mgr.load_proxies()

        clients = []
        for i in range(20):
            proxy = await proxy_mgr.get_next_proxy()
            client = FrontierClient(proxy=proxy, timeout=20.0)

            # Set shared token
            client.token = shared_token
            client.headers = shared_headers.copy()

            clients.append(client)

    print(f"Created {len(clients)} clients with shared token")

    # Concurrent requests
    routes = [
        ("DEN", "MCO"), ("ATL", "LAS"), ("DFW", "PHX"), ("MCO", "DEN"),
        ("LAS", "ATL"), ("PHX", "DFW"), ("DEN", "LAS"), ("ATL", "MCO"),
        ("DFW", "DEN"), ("MCO", "ATL"), ("LAS", "PHX"), ("PHX", "LAS"),
        ("DEN", "PHX"), ("ATL", "DEN"), ("DFW", "MCO"), ("MCO", "LAS"),
        ("LAS", "DEN"), ("PHX", "ATL"), ("DEN", "DFW"), ("ATL", "PHX"),
    ]

    date_str = str(datetime.now().date() + timedelta(days=7))

    async def make_request(client, origin, dest, idx):
        try:
            result = await client.search(origin, dest, date_str)
            return {"idx": idx, "status": "ok", "route": f"{origin}->{dest}"}
        except Exception as e:
            return {"idx": idx, "status": "fail", "route": f"{origin}->{dest}", "error": str(e)[:60]}

    print(f"\nMaking {len(routes)} concurrent requests...")
    start_time = datetime.now()

    tasks = [make_request(clients[i], origin, dest, i) for i, (origin, dest) in enumerate(routes)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = (datetime.now() - start_time).total_seconds()

    # Cleanup
    for client in clients:
        await client.close()

    # Analyze results
    success = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "ok")
    failed = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "fail")
    errors = sum(1 for r in results if isinstance(r, Exception))

    print(f"\nCompleted in {elapsed:.2f} seconds")
    print(f"Results: {success} success, {failed} failed, {errors} errors")

    if failed > 0 or errors > 0:
        print(f"\nFailed requests:")
        for r in results:
            if isinstance(r, dict) and r.get("status") == "fail":
                print(f"  {r['route']}: {r['error']}")

    return success >= 18  # Allow 2 failures for network issues

async def test_token_expiration():
    """Test how long a token stays valid"""
    print("\n" + "=" * 80)
    print("TEST 4: Token Expiration (requests over time)")
    print("=" * 80)

    client = FrontierClient()
    await client.authenticate()

    token = client.token
    print(f"Auth token: {token[:20]}...")

    # Make requests with delays
    intervals = [0, 30, 60, 120, 180]  # seconds

    for interval in intervals:
        if interval > 0:
            print(f"\nWaiting {interval} seconds...")
            await asyncio.sleep(interval)

        try:
            result = await client.search("DEN", "MCO", str(datetime.now().date() + timedelta(days=7)))
            print(f"  [OK] Request after {interval}s succeeded")
        except Exception as e:
            print(f"  [FAIL] Request after {interval}s failed: {e}")
            print(f"  Token may have expired")
            break

    await client.close()

async def test_optimization_savings():
    """Calculate potential savings with shared token approach"""
    print("\n" + "=" * 80)
    print("OPTIMIZATION ANALYSIS")
    print("=" * 80)

    # Current approach
    print("\nCurrent Approach (Lazy Auth):")
    print("  - 50 clients in pool")
    print("  - Each client authenticates once on first use")
    print("  - Total auth calls: 50")
    print("  - Each client reused ~80 times for 4000 routes")

    # Proposed approach
    print("\nProposed Approach (Shared Token):")
    print("  - 1 auth call to get token")
    print("  - Share token across all 50 clients")
    print("  - Total auth calls: 1 (or 2-3 if token expires)")
    print("  - Savings: 47-49 auth calls (~94-98% reduction)")

    print("\nImpact on RouteScraper:")
    print("  - Auth calls: 50 -> 1")
    print("  - Time saved: ~50 * 0.5s = ~25 seconds per chunk")
    print("  - For 4000 routes in 1 chunk: ~25 seconds faster")

async def main():
    print("\nTESTING SHARED AUTH TOKEN STRATEGY")
    print("=" * 80)

    test1 = await test_shared_token_single_proxy()
    test2 = await test_shared_token_multiple_clients()
    test3 = await test_concurrent_shared_token()

    # Skip test 4 for now (takes too long)
    # test4 = await test_token_expiration()

    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80)
    print(f"Test 1 (Single client, reused token): {'PASS' if test1 else 'FAIL'}")
    print(f"Test 2 (Multiple clients, shared token): {'PASS' if test2 else 'FAIL'}")
    print(f"Test 3 (Concurrent requests, shared token): {'PASS' if test3 else 'FAIL'}")

    if test1 and test2 and test3:
        print("\n" + "=" * 80)
        print("[SUCCESS] Shared token strategy is VIABLE!")
        print("=" * 80)
        print("\nRecommendations:")
        print("  1. Authenticate once to get a shared token")
        print("  2. Create client pool with shared token + headers")
        print("  3. If any request gets 401/403, re-authenticate and update all clients")
        print("  4. This reduces auth overhead by ~94% (50 calls -> 1 call)")
        await test_optimization_savings()
    else:
        print("\n[FAILED] Shared token strategy has issues")
        print("Stick with current lazy auth approach")

if __name__ == "__main__":
    asyncio.run(main())
