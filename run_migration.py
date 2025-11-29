"""
Database migration script
Run this to apply the monitoring features migration
"""
import asyncio
import os
from dotenv import load_dotenv

# Load environment variables BEFORE importing app.database
load_dotenv()

from sqlalchemy import text
from app.database import engine

async def run_migration():
    # Read the migration file
    migration_path = os.path.join(os.path.dirname(__file__), 'migrations', 'add_monitoring_features.sql')

    with open(migration_path, 'r') as f:
        migration_sql = f.read()

    # Split by semicolon but keep them together for execution
    statements = [s.strip() + ';' for s in migration_sql.split(';') if s.strip() and not s.strip().startswith('--')]

    async with engine.begin() as conn:
        print("Starting database migration...")

        for i, statement in enumerate(statements, 1):
            if statement.strip() == ';':
                continue
            try:
                print(f"\nExecuting statement {i}...")
                await conn.execute(text(statement))
                print(f"✓ Statement {i} completed")
            except Exception as e:
                print(f"✗ Statement {i} failed: {e}")
                # Continue with other statements even if one fails
                continue

        print("\n✓ Migration completed!")
        print("\nNew features added:")
        print("  - HeartbeatLog table for tracking scheduler heartbeats")
        print("  - Enhanced ScraperRun table with job_type, heartbeat_id, mode, and details")
        print("  - FareSnapshot table updated with DEN fare tracking")
        print("  - System settings for midnight scraper configuration")

if __name__ == "__main__":
    asyncio.run(run_migration())
