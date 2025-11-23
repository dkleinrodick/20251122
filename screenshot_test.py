import asyncio
import time
import subprocess
from playwright.async_api import async_playwright

async def run():
    # Start Mock Server
    server = subprocess.Popen(["python", "run_mock.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    print("Starting server on 8003...")
    time.sleep(8) # Wait longer

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        
        try:
            print("Navigating to One Way...")
            await page.goto("http://localhost:8003")
            await page.click("text=One Way")
            await page.wait_for_timeout(1000)
            await page.screenshot(path="oneway_desktop.png")
            print("Saved oneway_desktop.png")

            print("Navigating to Route Builder...")
            await page.click("text=Route Builder")
            await page.wait_for_timeout(1000)
            await page.screenshot(path="builder_desktop.png")
            print("Saved builder_desktop.png")

            print("Navigating to Explore...")
            await page.click("text=Explore")
            await page.wait_for_timeout(1000)
            await page.screenshot(path="explore_desktop.png")
            print("Saved explore_desktop.png")

            print("Navigating to Features...")
            await page.goto("http://localhost:8003/features")
            await page.wait_for_timeout(1000)
            await page.screenshot(path="features_desktop.png")
            print("Saved features_desktop.png")
        except Exception as e:
            print(f"Error: {e}")
            # Print server output
            try:
                outs, errs = server.communicate(timeout=1)
                print("Server Stdout:", outs.decode())
                print("Server Stderr:", errs.decode())
            except: pass
            raise

        await browser.close()

    server.terminate()
    print("Done.")

if __name__ == "__main__":
    asyncio.run(run())