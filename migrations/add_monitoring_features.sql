-- Migration: Add monitoring features and heartbeat tracking
-- Date: 2025-11-26

-- Create HeartbeatLog table
CREATE TABLE IF NOT EXISTS heartbeat_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    scrapers_triggered TEXT[],
    duration_ms FLOAT
);

-- Add new columns to scraper_runs table
ALTER TABLE scraper_runs
    ADD COLUMN IF NOT EXISTS job_type VARCHAR(100),
    ADD COLUMN IF NOT EXISTS heartbeat_id INTEGER REFERENCES heartbeat_logs(id),
    ADD COLUMN IF NOT EXISTS mode VARCHAR(50) DEFAULT 'manual',
    ADD COLUMN IF NOT EXISTS details JSONB;

-- Add DEN fare columns to fare_snapshots table
ALTER TABLE fare_snapshots
    ADD COLUMN IF NOT EXISTS min_price_den FLOAT,
    ADD COLUMN IF NOT EXISTS seats_den INTEGER;

-- Create indices for performance
CREATE INDEX IF NOT EXISTS idx_heartbeat_logs_timestamp ON heartbeat_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_scraper_runs_job_type ON scraper_runs(job_type);
CREATE INDEX IF NOT EXISTS idx_scraper_runs_heartbeat_id ON scraper_runs(heartbeat_id);
CREATE INDEX IF NOT EXISTS idx_scraper_runs_mode ON scraper_runs(mode);

-- Add two new system settings with defaults
INSERT INTO system_settings (key, value)
VALUES ('midnight_check_duration', '30')
ON CONFLICT (key) DO NOTHING;

INSERT INTO system_settings (key, value)
VALUES ('midnight_check_interval', '2')
ON CONFLICT (key) DO NOTHING;

-- Show tables created
SELECT 'Migration completed successfully' AS status;
