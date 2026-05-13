#!/usr/bin/env python3
"""发送通知 - 同时推送 Bark + 邮件"""
import os, sys, json, smtplib, requests
from email.mime.text import MIMEText
from email.header import Header

BARK_KEY = "kAj5L6s959apzPYTmrKmiE"
BARK_URL = "https://api.day.app/push"
SMTP_USER = "maoliang84@163.com"
SMTP_PASS = os.environ.get("SMTP_PASS", "")


def send_bark(title: str, body: str, group: str = "stock_monitor") -> bool:
    try:
        resp = requests.post(BARK_URL, json={
            "device_key": BARK_KEY,
            "title": title,
            "body": body,
            "group": group,
        }, timeout=10)
        return resp.json().get("code") == 200
    except Exception as e:
        print(f"[Bark] failed: {e}", file=sys.stderr)
        return False


def send_email(subject: str, body: str) -> bool:
    if not SMTP_PASS:
        print("[Email] SMTP_PASS not set", file=sys.stderr)
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = SMTP_USER
        msg["To"] = SMTP_USER
        msg["Subject"] = Header(subject, "utf-8")
        server = smtplib.SMTP_SSL("smtp.163.com", 465, timeout=10)
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [SMTP_USER], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"[Email] failed: {e}", file=sys.stderr)
        return False


def notify(title: str, body: str, group: str = "stock_monitor"):
    """同时发送 Bark 和邮件"""
    bark_ok = send_bark(title, body, group)
    email_ok = send_email(title, body)
    print(f"Bark: {'OK' if bark_ok else 'FAIL'} | Email: {'OK' if email_ok else 'FAIL'}")
    return bark_ok or email_ok


if __name__ == "__main__":
    usage = 'Usage: python send_notify.py "<title>" "<body>" [group]'
    if len(sys.argv) < 3:
        print(usage)
        sys.exit(1)
    title = sys.argv[1]
    body = sys.argv[2]
    group = sys.argv[3] if len(sys.argv) > 3 else "stock_monitor"
    notify(title, body, group)
