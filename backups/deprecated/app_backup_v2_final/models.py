from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, JSON, Date, Float
from sqlalchemy.sql import func
from app.database import Base

class SystemSetting(Base):
    __tablename__ = "system_settings"
    key = Column(String, primary_key=True, index=True)
    value = Column(String)
    # New Keys: "scraper_mode" (ondemand/cached), "auto_scrape_interval" (minutes), "midnight_scrape_enabled" (bool)

class Proxy(Base):
    __tablename__ = "proxies"
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, index=True)
    protocol = Column(String, default="http") # http, socks4, socks5
    is_active = Column(Boolean, default=True)
    latency_ms = Column(Integer, nullable=True)
    failure_count = Column(Integer, default=0)
    last_checked = Column(DateTime(timezone=True), onupdate=func.now())

class Airport(Base):
    __tablename__ = "airports"
    code = Column(String(3), primary_key=True)
    city_name = Column(String, nullable=True)
    city_code = Column(String, nullable=True) # To group airports like ORD/MDW
    timezone = Column(String, default="UTC") # e.g., "America/New_York"
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

class RoutePair(Base):
    __tablename__ = "route_pairs"
    id = Column(Integer, primary_key=True, index=True)
    origin = Column(String, index=True)
    destination = Column(String, index=True)
    last_validated = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True)
    last_error_at = Column(DateTime(timezone=True), nullable=True)
    error_count = Column(Integer, default=0)

class FlightCache(Base):
    __tablename__ = "flight_cache"
    id = Column(Integer, primary_key=True, index=True)
    origin = Column(String(3), index=True)
    destination = Column(String(3), index=True)
    travel_date = Column(String(10), index=True) # YYYY-MM-DD
    data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class WeatherData(Base):
    __tablename__ = "weather_data"
    id = Column(Integer, primary_key=True, index=True)
    airport_code = Column(String(3), index=True)
    date = Column(String(10), index=True) # YYYY-MM-DD
    temp_high = Column(Float)
    condition_code = Column(Integer) # OpenMeteo WMO code
    updated_at = Column(DateTime(timezone=True), server_default=func.now())

class ScraperRun(Base):
    __tablename__ = "scraper_runs"
    id = Column(Integer, primary_key=True, index=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Float, nullable=True)
    status = Column(String, default="running") # running, completed, failed
    total_routes = Column(Integer, default=0)
    routes_scraped = Column(Integer, default=0)
    routes_skipped = Column(Integer, default=0)
    routes_failed = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)