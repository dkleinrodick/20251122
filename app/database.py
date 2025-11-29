import os
import ssl
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
import asyncpg

# Default to local SQLite if no env var
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./gowild.db").strip()

# Force asyncpg for Postgres
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://")
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

# Remove sslmode parameter if present (asyncpg doesn't support it in URL)
if "?sslmode=" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.split("?")[0]

# Configure connection args
if "sqlite" in DATABASE_URL:
    connect_args = {"check_same_thread": False}
elif "postgresql" in DATABASE_URL:
    # asyncpg SSL configuration - require SSL for Supabase
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    connect_args = {
        "ssl": ssl_context,
        "server_settings": {
            "jit": "off",
            "statement_cache_size": "0"
        }
    }
else:
    connect_args = {}

# Create engine with NullPool for transaction pooler compatibility (no connection reuse = no prepared statement cache issues)
if "postgresql" in DATABASE_URL:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        connect_args=connect_args,
        poolclass=NullPool  # Use NullPool to avoid prepared statement issues with transaction pooler
    )
else:
    engine = create_async_engine(DATABASE_URL, echo=False, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)
Base = declarative_base()

async def get_db():
    async with SessionLocal() as session:
        yield session

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)