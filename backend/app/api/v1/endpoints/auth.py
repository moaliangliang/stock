"""
认证接口 - 登录、注册、用户管理"""
from __future__ import annotations
from typing import List
from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, get_current_active_superuser
from app.core.security import create_access_token
from app.schemas.user import LoginRequest, UserCreate, UserUpdate, UserSelfUpdate, UserResponse, TokenResponse, PasswordChangeRequest, APIKeyCreate, APIKeyResponse
from app.schemas.common import Response
from app.services.auth import authenticate_user, create_user, get_user_by_id, get_users, update_user, change_password, create_api_key, get_api_keys
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["认证管理"])


@router.post("/login", response_model=Response[TokenResponse])
async def login(
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """用户登录（form-encoded，经 Pydantic 校验）"""
    req = LoginRequest(username=username, password=password)
    user = await authenticate_user(db, req.username, req.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token = create_access_token(user.id)
    return Response(data=TokenResponse(access_token=token, user=UserResponse.model_validate(user)))


@router.post("/register", response_model=Response[UserResponse])
async def register(req: UserCreate, db: AsyncSession = Depends(get_db)):
    """用户注册"""
    user = await create_user(db, req.dict())
    return Response(data=UserResponse.model_validate(user), message="注册成功")


@router.get("/me", response_model=Response[UserResponse])
async def get_me(current_user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return Response(data=UserResponse.model_validate(current_user))


@router.put("/me", response_model=Response[UserResponse])
async def update_me(
    req: UserSelfUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新当前用户信息"""
    user = await update_user(db, current_user.id, req.dict(exclude_unset=True))
    return Response(data=UserResponse.model_validate(user))


@router.get("/users", response_model=Response[List[UserResponse]])
async def list_users(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_superuser),
):
    """获取用户列表（管理员）"""
    users = await get_users(db, skip, limit)
    return Response(data=[UserResponse.model_validate(u) for u in users])


@router.post("/api-keys", response_model=Response[APIKeyResponse])
async def create_api_key_endpoint(
    req: APIKeyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建API密钥"""
    api_key = await create_api_key(db, current_user.id, req)
    return Response(data=APIKeyResponse.model_validate(api_key), message="API密钥创建成功")


@router.post("/change-password", response_model=Response[dict])
async def change_password_endpoint(
    req: PasswordChangeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改当前用户密码"""
    ok = await change_password(db, current_user.id, req.current_password, req.new_password)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前密码错误")
    return Response(data={}, message="密码修改成功")


@router.get("/api-keys", response_model=Response[List[APIKeyResponse]])
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取API密钥列表"""
    keys = await get_api_keys(db, current_user.id)
    return Response(data=[APIKeyResponse.model_validate(k) for k in keys])
