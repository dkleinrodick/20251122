-- FlyGW Database Schema for Supabase (PostgreSQL)
-- Run this in Supabase SQL Editor after creating your project

-- Enable UUID extension (useful for future features)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- System Settings Table
CREATE TABLE IF NOT EXISTS system_settings (
    key VARCHAR PRIMARY KEY,
    value VARCHAR
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_system_settings_key ON system_settings(key);

-- Proxy Table
CREATE TABLE IF NOT EXISTS proxies (
    id SERIAL PRIMARY KEY,
    url VARCHAR UNIQUE NOT NULL,
    protocol VARCHAR DEFAULT 'http',
    is_active BOOLEAN DEFAULT TRUE,
    latency_ms INTEGER,
    failure_count INTEGER DEFAULT 0,
    last_checked TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_proxies_url ON proxies(url);
CREATE INDEX IF NOT EXISTS idx_proxies_is_active ON proxies(is_active);

-- Airport Table
CREATE TABLE IF NOT EXISTS airports (
    code VARCHAR(3) PRIMARY KEY,
    city_name VARCHAR,
    city_code VARCHAR,
    timezone VARCHAR DEFAULT 'UTC',
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION
);

-- Route Pairs Table
CREATE TABLE IF NOT EXISTS route_pairs (
    id SERIAL PRIMARY KEY,
    origin VARCHAR NOT NULL,
    destination VARCHAR NOT NULL,
    last_validated TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN DEFAULT TRUE,
    last_error_at TIMESTAMP WITH TIME ZONE,
    error_count INTEGER DEFAULT 0
);

-- Create indexes for route lookups
CREATE INDEX IF NOT EXISTS idx_route_pairs_origin ON route_pairs(origin);
CREATE INDEX IF NOT EXISTS idx_route_pairs_destination ON route_pairs(destination);
CREATE INDEX IF NOT EXISTS idx_route_pairs_is_active ON route_pairs(is_active);
CREATE INDEX IF NOT EXISTS idx_route_pairs_combo ON route_pairs(origin, destination);

-- Flight Cache Table
CREATE TABLE IF NOT EXISTS flight_cache (
    id SERIAL PRIMARY KEY,
    origin VARCHAR(3) NOT NULL,
    destination VARCHAR(3) NOT NULL,
    travel_date VARCHAR(10) NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for fast cache lookups
CREATE INDEX IF NOT EXISTS idx_flight_cache_origin ON flight_cache(origin);
CREATE INDEX IF NOT EXISTS idx_flight_cache_destination ON flight_cache(destination);
CREATE INDEX IF NOT EXISTS idx_flight_cache_date ON flight_cache(travel_date);
CREATE INDEX IF NOT EXISTS idx_flight_cache_combo ON flight_cache(origin, destination, travel_date);
CREATE INDEX IF NOT EXISTS idx_flight_cache_created ON flight_cache(created_at);

-- Scraper Runs Table
CREATE TABLE IF NOT EXISTS scraper_runs (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_seconds DOUBLE PRECISION,
    status VARCHAR(20),
    total_routes INTEGER,
    routes_scraped INTEGER,
    routes_skipped INTEGER,
    routes_failed INTEGER,
    error_message VARCHAR(500)
);

-- Index for scraper runs lookup
CREATE INDEX IF NOT EXISTS idx_scraper_runs_started_at ON scraper_runs(started_at);

-- Weather Data Table
CREATE TABLE IF NOT EXISTS weather_data (
    id SERIAL PRIMARY KEY,
    airport_code VARCHAR(3) NOT NULL,
    date VARCHAR(10) NOT NULL,
    temp_high DOUBLE PRECISION,
    condition_code INTEGER,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for weather lookups
CREATE INDEX IF NOT EXISTS idx_weather_airport ON weather_data(airport_code);
CREATE INDEX IF NOT EXISTS idx_weather_date ON weather_data(date);
CREATE INDEX IF NOT EXISTS idx_weather_combo ON weather_data(airport_code, date);

-- Insert default system settings
INSERT INTO system_settings (key, value) VALUES
    ('scraper_mode', 'ondemand'),
    ('max_concurrent_requests', '2'),
    ('cache_duration_minutes', '60'),
    ('auto_scrape_interval', '30'),
    ('midnight_scrape_enabled', 'true'),
    ('midnight_scrape_window', '15'),
    ('midnight_scrape_interval', '5'),
    ('cache_reset_enabled', 'true'),
    ('cache_reset_days', '2'),
    ('cache_reset_time', '09:00'),
    ('weather_scrape_time', '06:00'),
    ('weather_scrape_enabled', 'true'),
    ('weather_scrape_time_cst', '23:49'),
    ('admin_password', 'admin'),
    ('proxy_enabled', 'false'),
    ('scraper_popup_enabled', 'true'),
    ('save_debug_responses', 'false')
ON CONFLICT (key) DO NOTHING;

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'FlyGW database schema created successfully!';
    RAISE NOTICE 'Tables created: system_settings, proxies, airports, route_pairs, flight_cache, scraper_runs, weather_data';
END $$;
