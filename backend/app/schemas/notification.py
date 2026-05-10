"""
通知模块 Schema
"""
from typing import Any, Dict, Optional
from datetime import datetime
from pydantic import BaseModel


class NotificationResponse(BaseModel):
    """通知响应"""
    id: int
    user_id: int
    type: str
    title: str
    content: Optional[str] = None
    is_read: bool
    metadata_json: Dict[str, Any] = {}
    created_at: datetime

    class Config:
        from_attributes = True


class NotificationSettings(BaseModel):
    """通知偏好设置"""
    trade_executed: bool = True
    risk_triggered: bool = True
    strategy_error: bool = True
    system: bool = True
    email_enabled: bool = False
    email_address: Optional[str] = None
    webhook_url: Optional[str] = None
