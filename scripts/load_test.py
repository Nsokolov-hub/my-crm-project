import asyncio
import time
import httpx
import statistics
import sys

BASE_URL = "http://localhost:8000"

async def test_endpoint(client, url, name, times):
    latencies = []
    for _ in range(times):
        start = time.perf_counter()
        resp = await client.get(f"{BASE_URL}{url}")
        if resp.status_code != 200:
            print(f"Error {resp.status_code} on {url}: {resp.text}")
            continue
        latencies.append(time.perf_counter() - start)
    return name, latencies

async def simulate_user():
    async with httpx.AsyncClient(timeout=30.0, cookies={"crm_session": "test_load_token"}) as client:
        res = []
        res.append(await test_endpoint(client, "/api/v1/requests?limit=20&skip=0", "cards", 1))
        res.append(await test_endpoint(client, "/api/v1/requests?limit=20&commercial_stage=new", "filters", 1))
        res.append(await test_endpoint(client, "/api/v1/requests?limit=100&skip=0", "preview_100", 1))
        res.append(await test_endpoint(client, "/api/v1/analytics/dashboard?date_from=2025-01-01&date_to=2025-12-31", "annual_report", 1))
        return res

async def main():
    concurrent_users = 30
    print(f"Starting load test with {concurrent_users} users...")
    tasks = [simulate_user() for _ in range(concurrent_users)]
    results = await asyncio.gather(*tasks)
    
    agg = {"cards": [], "filters": [], "preview_100": [], "annual_report": []}
    for user_res in results:
        for name, latencies in user_res:
            if name in agg:
                agg[name].extend(latencies)
                
    for name, latencies in agg.items():
        if not latencies: continue
        p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) > 1 else latencies[0]
        avg = statistics.mean(latencies)
        print(f"Endpoint '{name}': P95 = {p95:.2f}s, Avg = {avg:.2f}s")
        
if __name__ == "__main__":
    asyncio.run(main())
