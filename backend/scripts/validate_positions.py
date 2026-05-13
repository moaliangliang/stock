#!/usr/bin/env python3
"""
持仓数据一致性校验脚本
交叉验证：DB持仓 ←→ mx-data 实时行情 ←→ 代码名称映射

A股交易规则备忘：
- 最小交易单位：100股（1手）
- ETF最小交易单位：100份
- 科创板最小交易单位：200股（1手=200股，688开头）
- 建议中的数量必须是100的整数倍
- 持仓≤100股的股票：只能全卖或不动，不存在"减半仓"操作

用法:
  cd /root/workspace/stock/backend
  source venv/bin/activate
  python3 scripts/validate_positions.py

输出:
  - 终端: 数据可信度摘要
  - Markdown 报告: scripts/output/validate_report_{date}.md
"""
import sys
import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# 把 mx-data skill 加进路径以便导入 MXData
SKILL_DIR = os.path.expanduser("~/skills/mx-data")
if SKILL_DIR not in sys.path:
    sys.path.insert(0, SKILL_DIR)
from mx_data import MXData

# ---- 配置 ----
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "quant_trade.db")
MAPPING_FILE = os.path.expanduser("~/.openclaw/workspace/mx_data/output/代码名称映射.md")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
BEIJING_TZ = timezone(timedelta(hours=8))

# 校验阈值
PRICE_DIFF_WARN = 0.01     # 价格偏差 >1% 告警
STALE_HOURS = 24            # 超过此时间未更新视为过期
NEGATIVE_COST_WARN = True   # 成本为负告警
HUGE_DAY_PNL = 0.20         # 单日涨跌 >20% 标记为异常


def load_mapping() -> dict[str, str]:
    """从代码名称映射文件解析 code→name"""
    mapping = {}
    if not os.path.exists(MAPPING_FILE):
        return mapping
    with open(MAPPING_FILE) as f:
        code = None
        for line in f:
            line = line.strip()
            if line.startswith("| ") and "|" in line[2:]:
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) >= 2:
                    c, n = parts[0], parts[1]
                    if c and n and c[0].isdigit():
                        code = c
                        mapping[c] = n
                    elif code and not c and n:
                        # 续行
                        pass
    return mapping


def parse_mapping_for_codes() -> dict[str, str]:
    """更稳健地解析映射表，返回 {bare_code: name}"""
    mapping = {}
    if not os.path.exists(MAPPING_FILE):
        return mapping
    with open(MAPPING_FILE) as f:
        content = f.read()
    import re
    for line in content.split("\n"):
        # 匹配: | 002463.SZ | 沪电股份 |  或  | 002463 | ... |
        m = re.match(r'\|\s*([\d]{6}(?:\.[A-Z]{2})?)\s*\|(.+?)(?:\||$)', line)
        if m:
            full_code = m.group(1)
            name = m.group(2).strip().rstrip("|").strip()
            # 提取纯数字代码
            bare = full_code.split(".")[0]
            if bare not in mapping:
                mapping[bare] = name
    return mapping


def read_positions() -> list[dict]:
    """从 SQLite 读取所有持仓"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, symbol, quantity, available_quantity, frozen_quantity,
               cost_price, current_price, market_value,
               pnl, pnl_ratio, day_pnl, day_pnl_ratio,
               updated_at
        FROM positions
        ORDER BY symbol
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def read_other_tables() -> dict:
    """读取关联表做交叉校验"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    result = {}

    # 订单
    cur.execute("SELECT symbol, side, quantity, filled_quantity, status, created_at FROM orders ORDER BY created_at DESC LIMIT 50")
    result["orders"] = [dict(r) for r in cur.fetchall()]

    # 成交
    cur.execute("SELECT symbol, side, price, quantity, trade_time FROM trades ORDER BY trade_time DESC LIMIT 50")
    result["trades"] = [dict(r) for r in cur.fetchall()]

    # 告警
    cur.execute("SELECT symbol, condition, target_price, status, triggered_at FROM price_alerts ORDER BY created_at DESC LIMIT 20")
    result["alerts"] = [dict(r) for r in cur.fetchall()]

    # 策略
    cur.execute("SELECT id, name, type, status, symbols FROM strategies")
    result["strategies"] = [dict(r) for r in cur.fetchall()]

    conn.close()
    return result


def _fetch_batch(batch: list[str]) -> dict[str, dict]:
    """查询单个批次的 mx-data（供并行调用）"""
    import re
    client = MXData()
    code_str = " ".join(batch)
    query = f"{code_str} 最新价 涨跌幅 名称 基金全称"
    batch_data = {}

    try:
        result = client.query(query)
    except Exception as e:
        print(f"  [WARN] mx-data 批次查询失败: {e}")
        return batch_data

    try:
        inner = result.get("data", {}).get("data", {})
        search = inner.get("searchDataResultDTO", {})
        dto_list = search.get("dataTableDTOList", [])
    except Exception:
        return batch_data

    for dto in dto_list:
        table = dto.get("table", {})
        name_map = dto.get("nameMap", {})
        head_names = table.get("headName", [])
        if not head_names:
            continue

        indicator_map = {}
        for field in dto.get("fieldSet", []):
            indicator_map[field["returnCode"]] = field["returnName"]

        col_keys = [k for k in table.keys() if k != "headName"]
        col_names = {}
        for k in col_keys:
            n = name_map.get(k, indicator_map.get(k, k))
            col_names[k] = str(n)

        for row_idx, entity_name in enumerate(head_names):
            code_match = re.search(r'\((\d{6}\.[A-Z]{2})\)', str(entity_name))
            if not code_match:
                continue
            code = code_match.group(1)

            row_data = {"_entity_name": str(entity_name)}
            for k in col_keys:
                vals = table.get(k, [])
                if row_idx < len(vals):
                    row_data[col_names[k]] = str(vals[row_idx])

            batch_data[code] = row_data
            bare = code.split(".")[0]
            if bare != code and bare not in batch_data:
                batch_data[bare] = row_data

    return batch_data


def fetch_market_data_batch(codes: list[str], batch_size: int = 6, max_workers: int = 4) -> dict[str, dict]:
    """并行查询 mx-data API，每批最多 batch_size 个代码"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    batches = [codes[i : i + batch_size] for i in range(0, len(codes), batch_size)]
    all_data = {}

    with ThreadPoolExecutor(max_workers=min(max_workers, len(batches))) as executor:
        futures = {executor.submit(_fetch_batch, batch): i for i, batch in enumerate(batches)}
        for future in as_completed(futures):
            try:
                batch_data = future.result()
                all_data.update(batch_data)
            except Exception as e:
                print(f"  [WARN] 并行批次失败: {e}")

    return all_data


# 保留兼容别名
fetch_market_data = fetch_market_data_batch


def validate(positions, market_data, mapping, other) -> dict:
    """执行所有校验，返回报告结构"""
    now = datetime.now(BEIJING_TZ)
    issues = defaultdict(list)
    stats = {
        "total": len(positions),
        "matched": 0,
        "name_mismatch": 0,
        "price_diff": 0,
        "negative_cost": 0,
        "stale": 0,
        "huge_day_pnl": 0,
        "total_market_value": 0,
        "total_pnl": 0,
        "total_day_pnl": 0,
    }

    validated = []

    for pos in positions:
        code = pos["symbol"]
        mv = pos["market_value"] or 0
        stats["total_market_value"] += mv
        stats["total_pnl"] += pos["pnl"] or 0
        stats["total_day_pnl"] += pos["day_pnl"] or 0

        entry = {
            "id": pos["id"],
            "code": code,
            "db_name": mapping.get(code, "?"),
            "api_name": "",
            "quantity": pos["quantity"],
            "cost_price": pos["cost_price"],
            "db_price": pos["current_price"],
            "api_price": None,
            "db_pnl": pos["pnl"],
            "db_pnl_pct": pos["pnl_ratio"],
            "db_day_pnl": pos["day_pnl"],
            "db_day_pnl_pct": pos["day_pnl_ratio"],
            "updated_at": pos["updated_at"],
            "flags": [],
        }

        # ---- 校验1：名称一致性 ----
        api_data = market_data.get(code, {})
        if api_data:
            stats["matched"] += 1
            api_entity = api_data.get("_entity_name", "")
            entry["api_name"] = api_entity
            db_name = mapping.get(code, "")
            # 宽松匹配：映射名去掉公司后缀后，API实体名应包含其核心词
            if db_name and api_entity:
                # 从映射名提取核心词（去掉常见基金公司后缀）
                core = db_name.replace("ETF", "").replace("东财", "").replace("华夏", "").replace("国泰", "").replace("广发", "").replace("博时", "").replace("永赢", "").replace("万家", "").strip()
                name_ok = (db_name in api_entity or
                           core in api_entity or
                           api_entity in db_name)
                if not name_ok:
                    stats["name_mismatch"] += 1
                    entry["flags"].append(f"名称不匹配: DB映射={db_name}, API={api_entity}")

            # ---- 校验2：现价偏差 ----
            raw_latest = api_data.get("最新价", api_data.get("收盘价", ""))
            if raw_latest:
                try:
                    api_price = float(raw_latest.replace(",", "").replace("元", ""))
                    entry["api_price"] = api_price
                    db_price = pos["current_price"]
                    if db_price and abs(db_price - api_price) / max(abs(api_price), 0.01) > PRICE_DIFF_WARN:
                        stats["price_diff"] += 1
                        diff_pct = (db_price - api_price) / api_price * 100
                        entry["flags"].append(f"现价偏差: DB={db_price:.3f} API={api_price:.3f} ({diff_pct:+.1f}%)")
                except ValueError:
                    pass

        # ---- 校验3：成本价异常 ----
        if pos["cost_price"] is not None and pos["cost_price"] < 0:
            stats["negative_cost"] += 1
            entry["flags"].append(f"成本为负: {pos['cost_price']:.3f}")

        # ---- 校验4：更新时间过期 ----
        if pos["updated_at"]:
            updated_str = str(pos["updated_at"])
            updated = datetime.fromisoformat(updated_str)
            # 如果时间戳带时区，转换；否则假定为北京时间
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=BEIJING_TZ)
            if (now - updated).total_seconds() > STALE_HOURS * 3600:
                stats["stale"] += 1
                hours_ago = (now - updated).total_seconds() / 3600
                entry["flags"].append(f"数据过期: {hours_ago:.0f}小时未更新")

        # ---- 校验5：单日涨跌幅异常 ----
        pnl_pct = pos["day_pnl_ratio"]
        if pnl_pct is not None and abs(pnl_pct) / 100 > HUGE_DAY_PNL:
            stats["huge_day_pnl"] += 1
            entry["flags"].append(f"单日涨跌异常: {pnl_pct:.2f}%")

        validated.append(entry)

    # ---- 交叉校验：订单/成交 vs 持仓 ----
    pos_codes = {p["symbol"] for p in positions}
    order_codes = {o["symbol"] for o in other.get("orders", [])}
    trade_codes = {t["symbol"] for t in other.get("trades", [])}

    orphan_orders = order_codes - pos_codes  # 有订单但无持仓
    orphan_trades = trade_codes - pos_codes  # 有成交但无持仓

    # ---- 告警交叉校验 ----
    alerts = other.get("alerts", [])
    active_alerts = [a for a in alerts if a["status"] == "active"]
    triggered_alerts = [a for a in alerts if a["status"] == "triggered"]

    report = {
        "generated_at": now.isoformat(),
        "stats": stats,
        "positions": validated,
        "orphan_orders": list(orphan_orders),
        "orphan_trades": list(orphan_trades),
        "active_alerts": active_alerts,
        "triggered_alerts": triggered_alerts,
        "strategies": other.get("strategies", []),
    }

    return report


def print_summary(report):
    """终端输出摘要"""
    s = report["stats"]
    print()
    print("=" * 60)
    print("  持仓数据一致性校验报告")
    print(f"  生成时间：{report['generated_at']}")
    print("=" * 60)
    print()
    print(f"  持仓总数: {s['total']}")
    print(f"  总市值:   {s['total_market_value']:,.1f}")
    print(f"  累计盈亏: {s['total_pnl']:+,.1f}")
    print(f"  今日盈亏: {s['total_day_pnl']:+,.1f}")
    print()
    print("  ── 校验结果 ──")
    print(f"  mx-data 行情匹配: {s['matched']}/{s['total']}")
    print(f"  {'✓' if s['name_mismatch'] == 0 else '✗'} 名称不匹配: {s['name_mismatch']}")
    print(f"  {'✓' if s['price_diff'] == 0 else '✗'} 现价偏差>1%: {s['price_diff']}")
    print(f"  {'✓' if s['negative_cost'] == 0 else '✗'} 成本为负: {s['negative_cost']}")
    print(f"  {'✓' if s['stale'] == 0 else '✗'} 数据过期: {s['stale']}")
    print(f"  {'✓' if s['huge_day_pnl'] == 0 else '✗'} 单日涨跌异常: {s['huge_day_pnl']}")

    flagged = [p for p in report["positions"] if p["flags"]]
    if flagged:
        print()
        print("  ── 异常明细 ──")
        for p in flagged:
            name = p["db_name"] if p["db_name"] != "?" else p["code"]
            print(f"  {p['code']} {name}")
            for f in p["flags"]:
                print(f"    ⚠ {f}")

    if report["orphan_orders"]:
        print(f"\n  ⚠ 孤立订单(无对应持仓): {', '.join(report['orphan_orders'])}")
    if report["orphan_trades"]:
        print(f"  ⚠ 孤立成交(无对应持仓): {', '.join(report['orphan_trades'])}")

    if report["active_alerts"]:
        print(f"\n  ── 活跃告警 ({len(report['active_alerts'])}个) ──")
        for a in report["active_alerts"]:
            print(f"  {a['symbol']} {a['condition']} {a['target_price']} — {a.get('message', '')}")

    print()
    bad = s["name_mismatch"] + s["price_diff"] + s["negative_cost"] + s["stale"] + s["huge_day_pnl"]
    if bad == 0:
        print("  ✓ 所有校验通过，数据可信")
    else:
        print(f"  ✗ 发现 {bad} 个问题，详见下方及报告文件")


def write_markdown_report(report):
    """输出 Markdown 报告"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    date_str = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
    path = os.path.join(OUTPUT_DIR, f"validate_report_{date_str}.md")

    s = report["stats"]
    lines = []
    lines.append(f"# 持仓数据校验报告 — {report['generated_at'][:10]}")
    lines.append("")
    lines.append("## 概览")
    lines.append("")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 持仓数 | {s['total']} |")
    lines.append(f"| 总市值 | {s['total_market_value']:,.1f} |")
    lines.append(f"| 累计盈亏 | {s['total_pnl']:+,.1f} |")
    lines.append(f"| mx-data匹配 | {s['matched']}/{s['total']} |")
    lines.append("")
    lines.append("## 校验结果")
    lines.append("")
    lines.append(f"| 检查项 | 状态 | 数量 |")
    lines.append(f"|--------|------|------|")
    lines.append(f"| 名称不匹配 | {'🔴' if s['name_mismatch'] else '🟢'} | {s['name_mismatch']} |")
    lines.append(f"| 现价偏差>1% | {'🔴' if s['price_diff'] else '🟢'} | {s['price_diff']} |")
    lines.append(f"| 成本为负 | {'🔴' if s['negative_cost'] else '🟢'} | {s['negative_cost']} |")
    lines.append(f"| 数据过期 | {'🔴' if s['stale'] else '🟢'} | {s['stale']} |")
    lines.append(f"| 单日涨跌异常 | {'🔴' if s['huge_day_pnl'] else '🟢'} | {s['huge_day_pnl']} |")
    lines.append("")

    flagged = [p for p in report["positions"] if p["flags"]]
    if flagged:
        lines.append("## 异常明细")
        lines.append("")
        lines.append("| 代码 | 名称 | 数量 | DB现价 | API现价 | 异常 |")
        lines.append("|------|------|------|--------|---------|------|")
        for p in flagged:
            name = p["db_name"] if p["db_name"] != "?" else "?"
            api_p = f"{p['api_price']:.3f}" if p["api_price"] else "无"
            lines.append(f"| {p['code']} | {name} | {p['quantity']:.0f} | {p['db_price']:.3f} | {api_p} | {'; '.join(p['flags'])} |")
        lines.append("")

    # 完整持仓清单
    lines.append("## 完整持仓清单")
    lines.append("")
    lines.append("| 代码 | 名称(映射) | API名称 | 数量 | 成本 | DB现价 | API现价 | 盈亏 | 今日盈亏 | 状态 |")
    lines.append("|------|-----------|---------|------|------|--------|---------|------|----------|------|")
    for p in report["positions"]:
        status = "✓" if not p["flags"] else "⚠"
        api_p = f"{p['api_price']:.3f}" if p["api_price"] else "-"
        cost = f"{p['cost_price']:.3f}" if p["cost_price"] else "-"
        lines.append(f"| {p['code']} | {p['db_name']} | {p['api_name'][:30] if p['api_name'] else '-'} | {p['quantity']:.0f} | {cost} | {p['db_price']:.3f} | {api_p} | {p['db_pnl']:+.1f}({p['db_pnl_pct']:+.2f}%) | {p['db_day_pnl']:+.1f}({p['db_day_pnl_pct']:+.2f}%) | {status} |")
    lines.append("")

    if report["active_alerts"]:
        lines.append("## 活跃告警")
        lines.append("")
        for a in report["active_alerts"]:
            lines.append(f"- {a['symbol']} {a['condition']} {a['target_price']} — {a.get('message', '')}")
        lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))

    print(f"\n  Markdown 报告: {path}")
    return path


def main():
    print("🔍 持仓数据一致性校验")
    print(f"   数据库: {DB_PATH}")
    print(f"   映射表: {MAPPING_FILE}")

    # 1. 加载名称映射
    mapping = parse_mapping_for_codes()
    print(f"   已加载映射: {len(mapping)} 条")

    # 2. 读取持仓
    positions = read_positions()
    if not positions:
        print("   (无持仓数据)")
        return
    codes = [p["symbol"] for p in positions]
    print(f"   持仓代码: {len(codes)} 个 → {codes}")

    # 3. 发现新代码
    known = set(mapping.keys())
    current = set(codes)
    new_codes = current - known
    if new_codes:
        print(f"   ⚠ 发现新代码: {new_codes}")
        print("   正在查询新代码名称...")

    # 4. 查询 mx-data
    print(f"   正在查询 mx-data 实时行情...")
    market_data = fetch_market_data(codes)
    print(f"   API 返回: {len(market_data)} 条行情数据")

    # 5. 更新名称映射
    for code in new_codes:
        if code in market_data:
            entity = market_data[code].get("_entity_name", code)
            mapping[code] = entity
            print(f"   + {code} → {entity}")

    # 6. 读取关联表
    other = read_other_tables()

    # 7. 执行校验
    report = validate(positions, market_data, mapping, other)

    # 8. 输出
    print_summary(report)
    write_markdown_report(report)


if __name__ == "__main__":
    main()
