import asyncio
import os
import sys
from dotenv import load_dotenv
import asyncpg
import ssl
from urllib.parse import urlparse
import certifi

async def verify():
    print("--- Environment Verification (With Certifi) ---")
    load_dotenv()
    
    db_url = os.environ.get("DATABASE_URL")
    
    if "@" in db_url:
        print(f"Target: {db_url.split('@')[1]}")
    
    try:
        # Parse logic 
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://")
        
        p = urlparse(db_url)
        
        # Use Certifi
        print(f"Using CA Bundle: {certifi.where()}")
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        
        print("Connecting...")
        conn = await asyncpg.connect(
            user=p.username,
            password=p.password,
            host=p.hostname,
            port=p.port,
            database=p.path.lstrip('/'),
            ssl=ssl_ctx
        )
        print("✅ Connection Successful!")
        await conn.close()
        
    except Exception as e:
        print(f"❌ Connection Failed: {e}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(verify())
