"""
Authentication service - user registration, login, and profile management."""
from __future__ import annotations
from typing import List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash, verify_password
from app.models.user import User, UserRole, APIKey


async def authenticate_user(
    db: AsyncSession, username: str, password: str
) -> Optional[User]:
    """
    Verify user credentials.

    Args:
        db: Database session.
        username: Username to authenticate.
        password: Plain-text password to verify.

    Returns:
        The User object if credentials are valid, None otherwise.
    """
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    if user is None:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        return None

    return user


async def create_user(db: AsyncSession, user_data: dict) -> User:
    """
    Create a new user.

    Args:
        db: Database session.
        user_data: Dictionary with fields: username, email, password,
                   and optionally nickname, role.

    Returns:
        The newly created User object.
    """
    user = User(
        username=user_data["username"],
        email=user_data["email"],
        hashed_password=get_password_hash(user_data["password"]),
        nickname=user_data.get("nickname"),
        role=user_data.get("role", UserRole.VIEWER),
        is_active=user_data.get("is_active", True),
        is_superuser=user_data.get("is_superuser", False),
        max_position_ratio=user_data.get("max_position_ratio", 30),
        max_daily_loss=user_data.get("max_daily_loss", 5),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    """
    Get a user by their ID.

    Args:
        db: Database session.
        user_id: ID of the user to retrieve.

    Returns:
        The User object if found, None otherwise.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_users(
    db: AsyncSession, skip: int = 0, limit: int = 100
) -> List[User]:
    """
    List users with pagination.

    Args:
        db: Database session.
        skip: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A list of User objects.
    """
    result = await db.execute(
        select(User).offset(skip).limit(limit).order_by(User.id)
    )
    return list(result.scalars().all())


async def update_user(
    db: AsyncSession, user_id: int, user_data: dict
) -> Optional[User]:
    """
    Update a user's profile.

    Only the fields provided in *user_data* will be updated.
    If a new password is provided it will be hashed before storage.

    Args:
        db: Database session.
        user_id: ID of the user to update.
        user_data: Dictionary of fields to update.

    Returns:
        The updated User object if found, None otherwise.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return None

    # Map of allowed update fields to their model attributes
    allowed_fields = {
        "email": "email",
        "nickname": "nickname",
        "role": "role",
        "is_active": "is_active",
        "is_superuser": "is_superuser",
        "max_position_ratio": "max_position_ratio",
        "max_daily_loss": "max_daily_loss",
    }

    for key, attr in allowed_fields.items():
        if key in user_data:
            setattr(user, attr, user_data[key])

    # Handle password separately
    if "password" in user_data and user_data["password"]:
        user.hashed_password = get_password_hash(user_data["password"])

    await db.flush()
    await db.refresh(user)
    return user


async def change_password(
    db: AsyncSession, user_id: int, current_password: str, new_password: str
) -> bool:
    """修改密码，需验证旧密码"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return False
    if not verify_password(current_password, user.hashed_password):
        return False
    user.hashed_password = get_password_hash(new_password)
    await db.flush()
    return True


async def create_api_key(db: AsyncSession, user_id: int, key_data: dict) -> APIKey:
    """创建新的 API 密钥"""
    api_key = APIKey(
        user_id=user_id,
        exchange=key_data["exchange"],
        api_key=key_data["api_key"],
        secret_key=key_data["secret_key"],
        passphrase=key_data.get("passphrase"),
        remark=key_data.get("remark"),
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)
    return api_key


async def get_api_keys(db: AsyncSession, user_id: int) -> list[APIKey]:
    """获取用户的 API 密钥列表"""
    result = await db.execute(
        select(APIKey).where(APIKey.user_id == user_id).order_by(APIKey.created_at.desc())
    )
    return list(result.scalars().all())
