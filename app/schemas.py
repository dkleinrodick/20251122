import uuid
from typing import Optional

from fastapi_users import schemas
from pydantic import Field

class UserRead(schemas.BaseUser[uuid.UUID]):
    city: Optional[str] = None
    state: Optional[str] = None
    marketing_opt_in: bool


class UserCreate(schemas.BaseUserCreate):
    city: Optional[str] = None
    state: Optional[str] = None
    marketing_opt_in: bool = Field(False, alias="marketingOptIn")
    email: str
    password: str


class UserUpdate(schemas.BaseUserUpdate):
    city: Optional[str] = None
    state: Optional[str] = None
    marketing_opt_in: Optional[bool] = Field(None, alias="marketingOptIn")
