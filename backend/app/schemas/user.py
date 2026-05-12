"""
用户模块 Schema
"""
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field

from app.models.user import UserRole


class LoginRequest(BaseModel):
    """登录请求"""
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)


class UserCreate(BaseModel):
    """创建用户"""
    username: str = Field(..., min_length=2, max_length=50)
    email: str = Field(..., max_length=100)
    password: str = Field(..., min_length=6, max_length=100)
    nickname: Optional[str] = None
    role: UserRole = UserRole.VIEWER


class UserSelfUpdate(BaseModel):
    """用户自助更新（不含敏感字段）"""
    email: Optional[str] = None
    nickname: Optional[str] = None
    password: Optional[str] = Field(None, min_length=6, max_length=100)
    max_position_ratio: Optional[int] = None
    max_daily_loss: Optional[int] = None


class UserUpdate(BaseModel):
    """管理员更新用户（含角色/状态控制）"""
    email: Optional[str] = None
    nickname: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    max_position_ratio: Optional[int] = None
    max_daily_loss: Optional[int] = None


class UserResponse(BaseModel):
    """用户响应"""
    id: int
    username: str
    email: str
    nickname: Optional[str] = None
    role: UserRole
    is_active: bool
    is_superuser: bool
    max_position_ratio: int
    max_daily_loss: int
    created_at: datetime

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    """令牌响应"""
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class PasswordChangeRequest(BaseModel):
    """修改密码请求"""
    current_password: str = Field(..., min_length=6, max_length=100)
    new_password: str = Field(..., min_length=6, max_length=100)


class APIKeyCreate(BaseModel):
    """创建API密钥"""
    exchange: str
    api_key: str
    secret_key: str
    passphrase: Optional[str] = None
    remark: Optional[str] = None


class APIKeyResponse(BaseModel):
    """API密钥响应"""
    id: int
    exchange: str
    api_key: str
    is_active: bool
    remark: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
