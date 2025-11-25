import asyncio
import os
import sys
import uuid
from dotenv import load_dotenv

# Load environment variables BEFORE importing database
# Try loading from .env in current dir
load_dotenv()

from sqlalchemy import select, update
from app.database import SessionLocal, init_db
from app.auth import User
from fastapi_users.password import PasswordHelper

async def make_user_superuser(email: str):
    print(f"Connecting to database... (DATABASE_URL present: {'Yes' if os.environ.get('DATABASE_URL') else 'No'})")
    if os.environ.get('DATABASE_URL'):
        # Mask password for display
        url = os.environ.get('DATABASE_URL')
        if "@" in url:
            print(f"Target: {url.split('@')[1]}")
    
    await init_db() 
    
    async with SessionLocal() as session:
        # Find the user
        stmt = select(User).where(User.email == email)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if user:
            if user.is_superuser:
                print(f"User {email} is already a superuser.")
            else:
                user.is_superuser = True
                session.add(user)
                await session.commit()
                print(f"User {email} has been promoted to superuser.")
        else:
            print(f"User {email} not found in this database.")
            print("Please register via the website first, or check if you are connecting to the right DB.")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python make_superuser.py <user_email>")
        sys.exit(1)
    
    user_email = sys.argv[1]
    asyncio.run(make_user_superuser(user_email))