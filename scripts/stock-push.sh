#!/bin/bash
# stock-push.sh - 持仓监控推送脚本
# 功能：
#   1. 每30分钟定时推送持仓汇总（9:30 10:00 10:30 11:00 11:30 13:00 13:30 14:00 14:30 15:00）
#   2. 单只日涨跌幅超过±5%时立即推送预警（同方向2小时内不重复）
# 数据来源：腾讯行情接口 qt.gtimg.cn
# 推送渠道：
#   - Bark → iPhone
# 用法: crontab 每5分钟执行一次: */5 9-15 * * 1-5 /path/to/stock-push.sh
#       ./stock-push.sh --test  手动测试推送（强制推送持仓汇总）

# 确保中文不乱码
export LANG="${LANG:-zh_CN.UTF-8}"
export LC_ALL="${LC_ALL:-zh_CN.UTF-8}"
export PYTHONIOENCODING=utf-8

# Bark 推送（iOS）
BARK_KEY="kAj5L6s959apzPYTmrKmiE"
BARK_URL="https://api.day.app/push"

STATE_FILE="/tmp/stock-push-alert-state.json"
TMPDIR="/tmp/stock-push-$$"
export TMPDIR

TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
WEEKDAY=$(date '+%u')
HOUR=$(date '+%H')
MINUTE=$(date '+%M')
NOW_MINUTES=$((10#${HOUR} * 60 + 10#${MINUTE}))

# --test 参数：强制推送，用于手动验证
FORCE_PUSH=false
if [ "$1" = "--test" ]; then
  FORCE_PUSH=true
  echo "[$TIMESTAMP] 测试模式：强制推送"
fi

# 周末跳过
if [ "$WEEKDAY" -gt 5 ]; then
  echo "[$TIMESTAMP] 非交易日，跳过"
  exit 0
fi

echo "[$TIMESTAMP] 开始采集行情..."
mkdir -p "$TMPDIR"

# ==========================================
# 1. 采集所有标的原始行情数据
# ==========================================
# 个股
curl -s --connect-timeout 8 --max-time 10 "http://qt.gtimg.cn/q=sh600028,sh600036,sh600789,sh601633" > "${TMPDIR}/individual.raw" 2>/dev/null &
# ETF
curl -s --connect-timeout 8 --max-time 10 "http://qt.gtimg.cn/q=sz159205,sh563230,sz159326,sz159516,sz159599,sz159637,sz159941,sh510050,sh510330,sh513180,sh513500,sh520530,sh560870" > "${TMPDIR}/etf.raw" 2>/dev/null &
# 关注（立讯精密、金风科技）
curl -s --connect-timeout 8 --max-time 10 "http://qt.gtimg.cn/q=sz002475,sz002202" > "${TMPDIR}/watch.raw" 2>/dev/null &
wait

# 合并
cat "${TMPDIR}/individual.raw" "${TMPDIR}/etf.raw" "${TMPDIR}/watch.raw" > "${TMPDIR}/all.raw"

# ==========================================
# 2. 生成持仓配置 JSON
# ==========================================
cat > "${TMPDIR}/config.json" << 'CONFIGJSON'
{
  "state_file": "__STATE_FILE__",
  "timestamp": "__TIMESTAMP__",
  "now_minutes": __NOW_MINUTES__,
  "scheduled_times": [570, 600, 630, 660, 690, 780, 810, 840, 870, 900],
  "schedule_tolerance": 2,
  "alert_threshold": 5.0,
  "alert_cooldown_hours": 2,
  "force_push": __FORCE_PUSH__,
  "holdings": [
    {"prefix": "sh", "code": "600028", "name": "中国石化", "shares": 500, "cost": 7.324},
    {"prefix": "sh", "code": "600036", "name": "招商银行", "shares": 200, "cost": 38.405},
    {"prefix": "sh", "code": "600789", "name": "鲁抗医药", "shares": 200, "cost": 17.398},
    {"prefix": "sh", "code": "601633", "name": "长城汽车", "shares": 300, "cost": 22.170},
    {"prefix": "sz", "code": "159205", "name": "创业东财", "shares": 7000, "cost": 1.709},
    {"prefix": "sh", "code": "563230", "name": "卫星ETF", "shares": 3000, "cost": 1.778},
    {"prefix": "sz", "code": "159326", "name": "电网设备", "shares": 1000, "cost": 2.061},
    {"prefix": "sz", "code": "159516", "name": "半导设备", "shares": 10200, "cost": 0.966},
    {"prefix": "sz", "code": "159599", "name": "芯片指数", "shares": 5100, "cost": 2.501},
    {"prefix": "sz", "code": "159637", "name": "新能龙头", "shares": 100, "cost": -0.166},
    {"prefix": "sz", "code": "159941", "name": "纳指ETF", "shares": 7600, "cost": 1.392},
    {"prefix": "sh", "code": "510050", "name": "50ETF", "shares": 3400, "cost": 3.257},
    {"prefix": "sh", "code": "510330", "name": "华夏300", "shares": 1200, "cost": 4.862},
    {"prefix": "sh", "code": "513180", "name": "恒指科技", "shares": 4200, "cost": 0.662},
    {"prefix": "sh", "code": "513500", "name": "标普500", "shares": 4700, "cost": 2.398},
    {"prefix": "sh", "code": "520530", "name": "港科ETF", "shares": 1000, "cost": 0.967},
    {"prefix": "sh", "code": "560870", "name": "工业有色", "shares": 5000, "cost": 1.968},
    {"prefix": "sz", "code": "002475", "name": "立讯精密", "shares": 0, "cost": 0},
    {"prefix": "sz", "code": "002202", "name": "金风科技", "shares": 0, "cost": 0}
  ]
}
CONFIGJSON

# 替换占位符
sed -i "s|__STATE_FILE__|${STATE_FILE}|g" "${TMPDIR}/config.json"
sed -i "s|__TIMESTAMP__|${TIMESTAMP}|g" "${TMPDIR}/config.json"
sed -i "s|__NOW_MINUTES__|${NOW_MINUTES}|g" "${TMPDIR}/config.json"
sed -i "s|__FORCE_PUSH__|${FORCE_PUSH}|g" "${TMPDIR}/config.json"

# ==========================================
# 3. Python 处理核心逻辑
# ==========================================
python3 << 'PYEOF'
import json, os, sys

CFG_PATH = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'config.json')
RAW_PATH = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'all.raw')

with open(CFG_PATH) as f:
    cfg = json.load(f)

STATE_FILE = cfg['state_file']
TIMESTAMP = cfg['timestamp']
NOW = cfg['now_minutes']
SCHEDULED = cfg['scheduled_times']
TOLERANCE = cfg['schedule_tolerance']
THRESHOLD = cfg['alert_threshold']
COOLDOWN = cfg['alert_cooldown_hours']
HOLDINGS = cfg['holdings']
FORCE_PUSH = cfg.get('force_push', False)

# --- 解析腾讯行情原始数据 ---
def parse_raw(code, prefix):
    """从合并的 raw 文件中查找指定代码的数据"""
    raw_prefix = f"{prefix}{code}"
    try:
        with open(RAW_PATH, 'rb') as f:
            content = f.read()
    except Exception:
        return None

    # 在原始内容中查找匹配行
    text = content.decode('gbk', errors='replace')
    for line in text.splitlines():
        if line.startswith(f"v_{raw_prefix}=") or f'"{raw_prefix}"' in line:
            start = line.find('"')
            end = line.rfind('"')
            if start < 0 or end < 0:
                continue
            fields = line[start+1:end].split('~')
            if len(fields) < 5:
                continue
            return {
                "name": fields[1] if len(fields) > 1 else "N/A",
                "price": float(fields[3]) if fields[3] else 0,
                "prev_close": float(fields[4]) if fields[4] else 0,
                "high": float(fields[33]) if len(fields) > 33 and fields[33] else 0,
                "low": float(fields[34]) if len(fields) > 34 and fields[34] else 0,
                "volume": fields[6] if len(fields) > 6 else "0",
            }
    return None

# --- 格式化价格 ---
def fmt_price(p):
    if p == 0:
        return "N/A"
    return f"{p:.3f}" if p < 10 else f"{p:.2f}"

# --- 涨跌幅箭头 ---
def arrow(pct):
    if pct > 0:
        return "📈"
    elif pct < 0:
        return "📉"
    return "➖"

# --- 加载状态文件（预警记录）---
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

# --- 保存状态文件 ---
def save_state(state):
    # 清理超过 24 小时的记录
    now_hour = NOW / 60
    to_del = []
    for code, info in state.items():
        # 检查时间格式: "2026-04-30 10:00"
        try:
            parts = info.get("time", "").split(" ")
            if len(parts) >= 2:
                h, m = parts[1].split(":")
                info_hour = int(h) + int(m) / 60
                if abs(now_hour - info_hour) > 12:
                    to_del.append(code)
        except:
            to_del.append(code)
    for code in to_del:
        del state[code]

    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, ensure_ascii=False)

# --- 判断是否为定时推送时间 ---
def is_scheduled_time():
    for t in SCHEDULED:
        if abs(NOW - t) <= TOLERANCE:
            return True
    return False

# --- 检查是否需要推送预警 ---
def check_alerts(data_dict, state):
    alerts = []
    for h in HOLDINGS:
        code = h['code']
        key = f"{h['prefix']}{code}"
        d = data_dict.get(key)
        if d is None or d['prev_close'] == 0 or d['price'] == 0:
            continue
        pct = (d['price'] - d['prev_close']) / d['prev_close'] * 100

        if abs(pct) < THRESHOLD:
            continue

        # 检查是否需要推送
        direction = "up" if pct > 0 else "down"
        last = state.get(key)
        should_alert = False

        if last is None:
            should_alert = True
        else:
            # 检查冷却期
            try:
                parts = last.get("time", "").split()
                if len(parts) >= 2:
                    hour_str, min_str = parts[1].split(":")
                    last_h = int(hour_str)
                    last_m = int(min_str)
                    last_total = last_h * 60 + last_m
                    elapsed = NOW - last_total
                    # 方向变化时立即重新预警；同方向需等待冷却时间
                    if last.get("direction") != direction:
                        should_alert = True
                    elif elapsed >= COOLDOWN * 60:
                        should_alert = True
            except:
                should_alert = True

        if should_alert:
            alerts.append({
                "key": key,
                "name": d["name"],
                "pct": pct,
                "price": d["price"],
                "direction": direction,
                "time": TIMESTAMP
            })
            # 更新状态
            state[key] = {"time": TIMESTAMP, "direction": direction, "change": round(pct, 2)}

    return alerts

# --- 生成持仓汇总 ---
def build_summary(data_dict):
    lines = []
    total_pnl = 0.0
    total_market_value = 0.0
    cat_lines = {"个股": [], "ETF": []}

    for h in HOLDINGS:
        code = h['code']
        key = f"{h['prefix']}{code}"
        d = data_dict.get(key)
        name = h['name']
        shares = h['shares']
        cost = h['cost']

        if d is None or d['price'] == 0:
            lines.append(f"➖ **{name}**：获取失败")
            continue

        price = d['price']
        prev = d['prev_close']

        # 日涨跌幅
        daily_pct = (price - prev) / prev * 100 if prev != 0 else 0

        # 成交量（腾讯接口字段6，单位：手）
        vol = int(d.get("volume", 0)) if d.get("volume", "").replace(',','').isdigit() else 0
        vol_str = f"{vol/10000:.2f}万" if vol >= 10000 else str(vol)

        # 持仓盈亏（成本价有效时计算）
        if cost != 0:
            pnl = (price - cost) * shares
            market_value = price * shares
            total_pnl += pnl
            total_market_value += market_value
            pnl_arrow = "📈" if pnl > 0 else "📉" if pnl < 0 else "➖"
            if cost > 0:
                cost_pct = (price - cost) / cost * 100
                pnl_str = f"({cost_pct:+.2f}%)"
            else:
                pnl_str = "(成本异常)"
            line = f"{arrow(daily_pct)} **{name}**：**{fmt_price(price)}** ({daily_pct:+.2f}%) | 量 {vol_str}手 | {pnl_arrow} {pnl:+.0f} {pnl_str}"
        else:
            line = f"{arrow(daily_pct)} **{name}**：**{fmt_price(price)}** ({daily_pct:+.2f}%) | 量 {vol_str}手"

        # 按类别分组
        # 6xxxxx=沪A, 0xxxxx/3xxxxx=深A/创业板 → 个股；其余为ETF
        if code.startswith('6') or code.startswith('0') or code.startswith('3'):
            cat_lines["个股"].append(line)
        else:
            cat_lines["ETF"].append(line)

    # 组装汇总
    out = [f"📊 **持仓汇总** {TIMESTAMP}", ""]

    for cat in ["个股", "ETF"]:
        if cat_lines[cat]:
            out.append(f"**【{cat}】**")
            for l in cat_lines[cat]:
                out.append(l)
            out.append("")

    if total_market_value > 0:
        overall_pnl_pct = total_pnl / (total_market_value - total_pnl) * 100 if (total_market_value - total_pnl) != 0 else 0
        total_arrow = "📈" if total_pnl > 0 else "📉" if total_pnl < 0 else "➖"
        out.append(f"**合计**：总市值 {total_market_value:.0f} | {total_arrow} 总盈亏 {total_pnl:+.0f} ({overall_pnl_pct:+.2f}%)")
        out.append("")

    out.append("")
    out.append(f"> 数据来源：腾讯行情 | {TIMESTAMP}")
    return "\n".join(out)

# --- 生成预警消息 ---
def build_alert(alerts):
    lines = ["⚠️ **行情异动提醒**", ""]
    for a in alerts:
        lines.append(f"{arrow(a['pct'])} **{a['name']}**：{fmt_price(a['price'])}（{a['pct']:+.2f}%）")
    lines.append("")
    lines.append(f"> {TIMESTAMP}")
    return "\n".join(lines)

# --- 主逻辑 ---
# 解析所有数据
all_keys = set()
for h in HOLDINGS:
    all_keys.add(f"{h['prefix']}{h['code']}")

data_dict = {}
for k in all_keys:
    prefix = k[:2]
    code = k[2:]
    d = parse_raw(code, prefix)
    if d:
        data_dict[k] = d

# 检查是否存在有效数据
has_data = any(d and d['price'] != 0 for d in data_dict.values())

# 加载状态
state = load_state()

# 需要推送的内容
push_title = ""
push_content = ""

# 1. 检查预警（有行情数据时才检查）
alerts = check_alerts(data_dict, state) if has_data else []
save_state(state)

# 2. 判断是否为定时推送时间
is_scheduled = is_scheduled_time()

# 3. 构造推送内容
if alerts and (is_scheduled or FORCE_PUSH):
    # 预警 + 汇总合并
    push_title = f"⚠️ 异动预警 + 持仓汇总 {TIMESTAMP}"
    content_parts = [build_alert(alerts), "", "=" * 20, "", build_summary(data_dict)]
    push_content = "\n".join(content_parts)
elif alerts:
    push_title = f"⚠️ 行情异动预警 {TIMESTAMP}"
    push_content = build_alert(alerts)
elif is_scheduled or FORCE_PUSH:
    push_title = f"📊 持仓汇总 {TIMESTAMP}"
    push_content = build_summary(data_dict)

# 写推送文件
if push_title and push_content:
    out_path = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'push.json')
    with open(out_path, 'w') as f:
        json.dump({"title": push_title, "content": push_content}, f, ensure_ascii=False)
    print(f"[{TIMESTAMP}] 推送内容已生成：{push_title}")
else:
    print(f"[{TIMESTAMP}] 无需推送")
    # 写空文件标记
    out_path = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'push.json')
    with open(out_path, 'w') as f:
        json.dump({}, f)

PYEOF

# ==========================================
# 4. 推送到各渠道
# ==========================================
if [ -f "${TMPDIR}/push.json" ]; then
  TITLE=$(python3 -c "import json; print(json.load(open('${TMPDIR}/push.json')).get('title',''))")
  CONTENT=$(python3 -c "import json; print(json.load(open('${TMPDIR}/push.json')).get('content',''))")

  if [ -z "$TITLE" ]; then
    echo "[$TIMESTAMP] 无需推送"
  else
    echo "[$TIMESTAMP] 开始推送：${TITLE}"

    # --- Bark（iOS）---
    if [ -n "$BARK_KEY" ]; then
      echo "[$TIMESTAMP] 推送到 Bark..."
      # 用 Python 生成 Bark 的 JSON，避免 shell 转义问题
      python3 << EOF 2>/dev/null
import json, os
with open(os.environ['TMPDIR'] + '/push.json') as f:
    data = json.load(f)
bark_payload = {
    "device_key": "${BARK_KEY}",
    "title": data.get("title", ""),
    "body": data.get("content", ""),
    "badge": 1,
    "sound": "default"
}
with open(os.environ['TMPDIR'] + '/bark_push.json', 'w') as f:
    json.dump(bark_payload, f, ensure_ascii=False)
EOF
      curl -s -X POST "$BARK_URL" \
        -H "Content-Type: application/json; charset=utf-8" \
        -d "@${TMPDIR}/bark_push.json" 2>/dev/null
      echo ""
    fi

    echo "[$TIMESTAMP] 推送完成"
  fi
fi

# 清理
rm -rf "$TMPDIR"
