from typing import Optional
import uuid

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import AuthenticationBackend, CookieTransport, JWTStrategy
from fastapi_users.db import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from sqlalchemy import Column, String, Boolean, Integer, DateTime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.database import Base, get_db

# Configuration from main.py or .env for JWT
import os
SECRET = os.environ.get("USERS_SECRET", "default_user_secret_change_me_in_production") # TODO: Use a proper secret in production
# NOTE: The current JWT_SECRET from main.py is for Admin only. We will use a separate,
# dedicated secret for user authentication.

# 1. Database User Model
class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"
    city = Column(String(255), nullable=True)
    state = Column(String(255), nullable=True)
    marketing_opt_in = Column(Boolean, default=False, nullable=False)
    live_search_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# 2. User Manager
class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET # TODO: Use a separate secret
    verification_token_secret = SECRET # TODO: Use a separate secret

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        print(f"User {user.id} has registered.")

    async def on_after_forgot_password(self, user: User, token: str, request: Optional[Request] = None):
        print(f"User {user.id} has requested a password reset token: {token}")

    async def on_after_request_verify(self, user: User, token: str, request: Optional[Request] = None):
        print(f"Verification requested for user {user.id}. Token: {token}")
        # In a real application, you would send an email here.
        # For now, we'll just print it.
        # Example: await send_verification_email(user.email, token)

    # Mock Email Sender (for development)
    async def _send_verification_email(self, email: str, token: str):
        print(f"Mock email sent to {email} with verification token: {token}")
        print(f"Verification Link: /auth/verify?token={token}") # Frontend would navigate here


async def get_user_db(session: AsyncSession = Depends(get_db)):
    yield SQLAlchemyUserDatabase(session, User)

async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)

# 3. Authentication Backend
cookie_transport = CookieTransport(cookie_name="wildfares_b", cookie_max_age=3600 * 24 * 7, cookie_secure=True, cookie_httponly=True) # 7 days

def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=SECRET, lifetime_seconds=3600 * 24 * 7) # 7 days

auth_backend = AuthenticationBackend(
    name="jwt_cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

# 4. FastAPIUsers instance
fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

# Dependencies for protecting routes
current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
