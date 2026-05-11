"""
报告下载接口 - 关联 mx_data 分析报告和回测结果到前端
"""
from __future__ import annotations
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.schemas.common import Response

router = APIRouter(prefix="/reports", tags=["分析报告"])

# 报告目录
REPORT_DIR = Path("/root/.openclaw/workspace/mx_data/output")
ALERT_LOG = Path("/root/workspace/stock/buy_signal_alerts.log")
STATE_FILE = Path("/root/workspace/stock/buy_signal_state.json")


class ReportItem(BaseModel):
    name: str
    size: int
    size_display: str
    modified: str
    type: str
    download_url: str


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    elif size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


@router.get("", response_model=Response[List[ReportItem]])
async def list_reports(
    keyword: Optional[str] = Query(None, description="搜索关键词"),
    file_type: Optional[str] = Query(None, description="文件类型: xlsx/csv/json/txt"),
):
    """列出所有分析报告"""
    items = []

    files_to_list = []
    if REPORT_DIR.exists():
        files_to_list.extend(REPORT_DIR.iterdir())

    for fp in files_to_list:
        if not fp.is_file():
            continue
        stat = fp.stat()
        ext = fp.suffix.lower().lstrip(".")

        if keyword and keyword.lower() not in fp.name.lower():
            continue
        if file_type and ext != file_type.lower():
            continue

        items.append(ReportItem(
            name=fp.name,
            size=stat.st_size,
            size_display=_format_size(stat.st_size),
            modified=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            type=ext,
            download_url=f"/api/v1/reports/download/{fp.name}",
        ))

    # Sort by modified time descending
    items.sort(key=lambda x: x.modified, reverse=True)
    return Response(data=items)


@router.get("/download/{filename}")
async def download_report(filename: str):
    """下载指定报告文件"""
    safe_path = (REPORT_DIR / filename).resolve()
    if not str(safe_path).startswith(str(REPORT_DIR.resolve())):
        return Response(code=403, message="禁止访问").dict()

    if not safe_path.exists():
        return Response(code=404, message="文件不存在").dict()

    content_type_map = {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv": "text/csv",
        "json": "application/json",
        "txt": "text/plain",
    }
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    media_type = content_type_map.get(ext, "application/octet-stream")

    return FileResponse(
        path=str(safe_path),
        filename=filename,
        media_type=media_type,
    )


@router.get("/alert-log")
async def get_alert_log():
    """获取买入信号提醒日志 + 当前最新价格"""
    result = {"content": "暂无买入信号日志", "updated": None, "current_prices": None}

    if ALERT_LOG.exists():
        stat = ALERT_LOG.stat()
        content = ALERT_LOG.read_text(encoding="utf-8")
        result["content"] = content[-10000:] if len(content) > 10000 else content
        result["updated"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            result["current_prices"] = {
                "updated": state.get("updated"),
                "stocks": state.get("stocks", {}),
                "active_signals": state.get("active_signals", []),
            }
        except (json.JSONDecodeError, KeyError):
            pass

    return Response(data=result)
