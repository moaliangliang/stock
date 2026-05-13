"""共享通知工具 — Bark 推送 + 邮件发送。"""
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from typing import Optional

import requests
from loguru import logger

from app.core.config import settings

BARK_URL = "https://api.day.app/push"


def push_bark(title: str, body: str, group: str = "quant") -> bool:
    """通过 Bark 推送通知到手机。"""
    if not settings.BARK_KEY:
        logger.warning("BARK_KEY 未配置，跳过 Bark 推送")
        return False
    payload = {
        "device_key": settings.BARK_KEY,
        "title": title,
        "body": body,
        "badge": 1,
        "sound": "default",
        "group": group,
    }
    try:
        resp = requests.post(BARK_URL, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info(f"Bark推送成功: {title}")
            return True
        else:
            logger.warning(f"Bark推送失败: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Bark推送异常: {e}")
        return False


def send_email(subject: str, body: str, to_email: Optional[str] = None) -> bool:
    """通过 SMTP 发送邮件通知。"""
    if not settings.SMTP_PASS:
        logger.warning("SMTP_PASS 未配置，跳过邮件发送")
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = settings.SMTP_USER
        msg["To"] = to_email or settings.NOTIFY_EMAIL
        msg["Subject"] = Header(subject, "utf-8")
        server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
        server.login(settings.SMTP_USER, settings.SMTP_PASS)
        server.sendmail(settings.SMTP_USER, [msg["To"]], msg.as_string())
        server.quit()
        logger.info(f"邮件发送成功: {subject}")
        return True
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
        return False


def push_both(title: str, body: str, group: str = "quant") -> None:
    """同时通过 Bark 和邮件推送通知。"""
    push_bark(title, body, group)
    send_email(title, body)
