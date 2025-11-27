from sqlalchemy import func, cast, Date, delete, select
from app.models import FareSnapshot
from app.database import get_db, SessionLocal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import logging
from datetime import datetime

# Logic for getting collection dates
async def get_collection_dates_logic(db: AsyncSession):
    # Group by date part of scraped_at
    # casting to Date works in Postgres and SQLite usually
    stmt = select(
        cast(FareSnapshot.scraped_at, Date).label("date"), 
        func.count(FareSnapshot.id).label("count")
    ).group_by("date").order_by("date")
    
    res = await db.execute(stmt)
    rows = res.all()
    
    return [{"date": str(r.date), "count": r.count} for r in rows if r.date is not None]

async def delete_collection_date_logic(date_str: str, db: AsyncSession):
    # date_str is YYYY-MM-DD
    # Fix: Convert string to python date object so sqlalchemy/asyncpg binds it as DATE type
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    
    stmt = delete(FareSnapshot).where(cast(FareSnapshot.scraped_at, Date) == target_date)
    res = await db.execute(stmt)
    await db.commit()
    return res.rowcount
