import asyncio
import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, text, update
from app.models import SystemSetting

# Load env to get DATABASE_URL
load_dotenv()

async def check_admin_pass():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set in .env")
        return

    # Fix URL for asyncpg
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://")
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://")
    
    # Insecure SSL context for script
    import ssl
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    
    engine = create_async_engine(db_url, connect_args={"ssl": ssl_ctx})
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with async_session() as session:
        print("Checking 'admin_password' in system_settings...")
        stmt = select(SystemSetting).where(SystemSetting.key == "admin_password")
        result = await session.execute(stmt)
        setting = result.scalar_one_or_none()
        
        if setting:
            print(f"❌ FOUND DB OVERRIDE: '{setting.value}'")
            print("This value is overriding your environment variable.")
            
            # Update it to the correct one
            print(f"Updating to 'cab63wiaV!'...")
            setting.value = "cab63wiaV!"
            await session.commit()
            print("✅ Database updated. Try logging in now.")
        else:
            print("✅ No DB override found. Application should be using ADMIN_PASSWORD from env.")

if __name__ == "__main__":
    asyncio.run(check_admin_pass())
