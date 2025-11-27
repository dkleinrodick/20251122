import asyncio
import asyncpg
import ssl

DATABASE_URL = "postgresql://postgres.vwtxmyboywwncglunrxv:egPI3VHuOf4QHSjv@aws-0-us-east-1.pooler.supabase.com:6543/postgres"

async def test_connection():
    print(f"Connecting to: {DATABASE_URL}")
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    try:
        conn = await asyncpg.connect(DATABASE_URL, ssl=ssl_context)
        print("Successfully connected!")
        version = await conn.fetchval("SELECT version()")
        print(f"Database version: {version}")
        await conn.close()
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())