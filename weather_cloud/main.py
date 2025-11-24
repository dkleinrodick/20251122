import sys
import os
import asyncio
import logging
import ssl
from datetime import datetime
import pytz
import httpx
from dotenv import load_dotenv

load_dotenv()

# Add parent directory to path so we can import 'app'
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy import select

from app.models import SystemSetting, Airport, WeatherData
from app.airports_data import AIRPORTS_LIST

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weather_cloud")

# Database Config (Session Mode)
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable not set")
    import sys
    sys.exit(1)

# Force asyncpg for Postgres
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://")
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

# Remove sslmode parameter if present (asyncpg doesn't support it in URL)
if "?sslmode=" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.split("?")[0]

# Strip whitespace just in case
DATABASE_URL = DATABASE_URL.strip()

# Configure SSL for Supabase
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE
connect_args = {
    "ssl": ssl_context,
    "server_settings": {
        "jit": "off"
    }
}

# Create engine with NullPool for transaction pooler compatibility
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args=connect_args,
    poolclass=NullPool
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)

async def get_setting(session, key, default):
    res = await session.execute(select(SystemSetting).where(SystemSetting.key == key))
    s = res.scalar_one_or_none()
    return s.value if s else default

async def update_weather_data(session):
    """
    Fetches weather for all active destinations using Open-Meteo.
    Clears old data first, then repopulates.
    """
    logger.info("Clearing old weather data...")
    await session.execute(delete(WeatherData))
    await session.commit()

    logger.info("Updating Weather Data...")
    async with httpx.AsyncClient() as client:
        chunk_size = 10
        for i in range(0, len(AIRPORTS_LIST), chunk_size):
            chunk = AIRPORTS_LIST[i:i + chunk_size]
            tasks = []
            for entry in chunk:
                code = entry["code"]
                lat = entry.get("lat")
                lon = entry.get("lon")
                if not lat or not lon: continue

                # forecast_days=16 covers the user's 10-day requirement safely
                url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=weathercode,temperature_2m_max&timezone=auto&forecast_days=16"
                tasks.append(client.get(url))
            
            # Execute chunk
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            
            for j, resp in enumerate(responses):
                if isinstance(resp, Exception):
                    logger.error(f"Weather fetch error: {resp}")
                    continue
                
                if resp.status_code == 200:
                    data = resp.json()
                    daily = data.get("daily", {})
                    times = daily.get("time", [])
                    codes = daily.get("weathercode", [])
                    temps = daily.get("temperature_2m_max", [])
                    
                    entry = chunk[j]
                    code = entry["code"]
                    
                    for k, date_str in enumerate(times):
                        # Upsert logic
                        stmt = select(WeatherData).where(WeatherData.airport_code == code, WeatherData.date == date_str)
                        res = await session.execute(stmt)
                        wd = res.scalar_one_or_none()
                        
                        if not wd:
                            wd = WeatherData(airport_code=code, date=date_str, condition_code=codes[k], temp_high=temps[k])
                            session.add(wd)
                        else:
                            wd.condition_code = codes[k]
                            wd.temp_high = temps[k]
                            wd.updated_at = datetime.utcnow()
            
            await session.commit()
            await asyncio.sleep(0.2) # Brief pause to respect rate limits

    logger.info("Weather data update complete.")

async def main():
    logger.info("Starting Cloud Weather Scraper...")

    async with SessionLocal() as session:
        # 1. Load Settings
        enabled = (await get_setting(session, "weather_scrape_enabled", "true")).lower() == "true"

        if not enabled:
            logger.info("Weather scraper is disabled in settings. Exiting.")
            return

        # 2. Run Weather Update
        await update_weather_data(session)

        logger.info("Cloud Weather Scraper Cycle Complete.")

if __name__ == "__main__":
    asyncio.run(main())
