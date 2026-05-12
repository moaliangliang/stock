#!/usr/bin/env python3
"""
东方财富 easytrader 交易代理服务 — 运行在 Windows 机器上

将 easytrader 封装为 HTTP 服务，供 Linux 后端通过局域网调用。

启动前：
  1. 打开东方财富独立交易客户端并登录
  2. pip install easytrader flask
  3. python eastmoney_agent.py

默认监听 0.0.0.0:8520，建议只在局域网使用，务必配置防火墙限制外部访问。
"""
import logging
import os
import threading

from flask import Flask, request, jsonify

# ---------------------------------------------------------------------------
# easytrader 初始化
# ---------------------------------------------------------------------------

app = Flask(__name__)
logger = logging.getLogger("eastmoney_agent")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_trader = None
_trader_lock = threading.Lock()


def _get_trader():
    """延迟初始化 easytrader 实例（线程安全）"""
    global _trader
    if _trader is not None:
        return _trader
    with _trader_lock:
        if _trader is not None:
            return _trader
        try:
            import easytrader
        except ImportError:
            raise RuntimeError("请先安装 easytrader: pip install easytrader")

        # 东方财富交易客户端底层是同花顺内核，统一用 ths
        _trader = easytrader.use("ths")
        # 新版 easytrader (>=0.23) 用 connect() 替代 prepare()
        # 不传 exe_path，自动按窗口标题查找已登录的客户端
        _trader.connect()
        logger.info("easytrader 初始化完成，已连接东方财富客户端")
        return _trader


def _safe_call(fn, *args, **kwargs):
    """统一错误处理"""
    try:
        trader = _get_trader()
        result = fn(trader, *args, **kwargs)
        return {"ok": True, "data": result}
    except Exception as e:
        logger.exception("交易操作失败")
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "trader_ready": _trader is not None})


@app.route("/account", methods=["GET"])
def account():
    """获取账户资金信息"""
    return jsonify(_safe_call(lambda t: t.balance))


@app.route("/position", methods=["GET"])
def positions():
    """获取持仓列表"""
    return jsonify(_safe_call(lambda t: t.position))


@app.route("/order", methods=["POST"])
def create_order():
    """
    下单
    Body: {"symbol": "600519", "side": "buy"|"sell", "price": 1600.0, "amount": 100}
    """
    body = request.get_json(force=True)
    symbol = body["symbol"]
    side = body["side"]
    price = float(body["price"])
    amount = int(body.get("amount", 100))

    if side == "buy":
        return jsonify(_safe_call(lambda t: t.buy(symbol, price, amount)))
    elif side == "sell":
        return jsonify(_safe_call(lambda t: t.sell(symbol, price, amount)))
    else:
        return jsonify({"ok": False, "error": f"不支持的交易方向: {side}"}), 400


@app.route("/cancel", methods=["POST"])
def cancel_order():
    """
    撤单
    Body: {"entrust_no": "委托编号"}
    """
    body = request.get_json(force=True)
    entrust_no = body["entrust_no"]
    return jsonify(_safe_call(lambda t: t.cancel_entrust(entrust_no)))


@app.route("/orders/today", methods=["GET"])
def today_orders():
    """当日委托"""
    return jsonify(_safe_call(lambda t: t.today_entrusts))


@app.route("/trades/today", methods=["GET"])
def today_trades():
    """当日成交"""
    return jsonify(_safe_call(lambda t: t.today_trades))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.environ.get("EM_AGENT_HOST", "0.0.0.0")
    port = int(os.environ.get("EM_AGENT_PORT", "8520"))

    print(f"""
    ╔══════════════════════════════════════════════╗
    ║  东方财富 easytrader 交易代理              ║
    ╚══════════════════════════════════════════════╝

    启动前请确保：
      1. 东方财富交易客户端已打开并登录
      2. easytrader 已安装: pip install easytrader

    代理地址: http://{host}:{port}
    """)

    # Flask 内置服务器仅适合开发/局域网，生产环境请用 waitress
    app.run(host=host, port=port, debug=False)
