import asyncio
import logging
from app.scheduler import update_weather_data
from app.database import init_db

# Setup logging
logging.basicConfig(level=logging.INFO)

async def main():
    await init_db()
    await update_weather_data()

if __name__ == "__main__":
    asyncio.run(main())
