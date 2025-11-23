import httpx
import asyncio

async def test_auth():
    url = "https://mtier.flyfrontier.com/registrationssv/RetrieveAnonymousToken"
    
    configs = [
        {"name": "Original", "headers": {"User-Agent": "NCPAndroid/3.3.0", "ocp-apim-subscription-key": "493f95d2aa20409e9094b6ae78c1e5de"}},
        {"name": "Newer App", "headers": {"User-Agent": "NCPAndroid/3.5.0", "ocp-apim-subscription-key": "493f95d2aa20409e9094b6ae78c1e5de"}},
        {"name": "Browser UA", "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "ocp-apim-subscription-key": "493f95d2aa20409e9094b6ae78c1e5de"}},
        {"name": "No Key", "headers": {"User-Agent": "NCPAndroid/3.3.0"}}
    ]

    for cfg in configs:
        print(f"\nTesting {cfg['name']}...")
        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.post(url, headers=cfg['headers'])
                print(f"Status: {resp.status_code}")
                if resp.status_code == 200:
                    print("SUCCESS!")
                    break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_auth())