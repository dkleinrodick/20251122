import asyncio
import os
import sys
from dotenv import load_dotenv
import asyncpg
import ssl
from urllib.parse import urlparse

async def verify():
    print("--- Environment Verification ---")
    load_dotenv()
    
    db_url = os.environ.get("DATABASE_URL")
    admin_pass = os.environ.get("ADMIN_PASSWORD")
    
    print(f"DATABASE_URL found: {'YES' if db_url else 'NO'}")
    print(f"ADMIN_PASSWORD found: {'YES' if admin_pass else 'NO'}")
    
    if not db_url:
        print("❌ Error: DATABASE_URL is missing in .env")
        return

    # Masked preview
    if "@" in db_url:
        print(f"Target: {db_url.split('@')[1]}")
    
    print("\n--- Testing Database Connection (AsyncPG) ---")
    try:
        # Parse logic mimicking app/database.py
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://")
        
        # Extract params
        p = urlparse(db_url)
        
        ssl_ctx = ssl.create_default_context()
        # Default is verify_mode = CERT_REQUIRED, check_hostname = True
        
        print("Connecting with SSL verification enabled...")
        conn = await asyncpg.connect(
            user=p.username,
            password=p.password,
            host=p.hostname,
            port=p.port,
            database=p.path.lstrip('/'),
            ssl=ssl_ctx
        )
        print("✅ Connection Successful!")
        
        ver = await conn.fetchval("SELECT version()")
        print(f"Server Version: {ver}")
        
        await conn.close()
        
    except Exception as e:
        print(f"❌ Connection Failed: {e}")
        print("\nTroubleshooting:")
        print("1. Check if your IP is allowed in Supabase.")
        print("2. Verify the password is correct.")
        print("3. Ensure the hostname is reachable.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(verify())