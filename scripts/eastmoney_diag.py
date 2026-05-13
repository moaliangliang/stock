#!/usr/bin/env python3
"""东方财富 easytrader 诊断脚本 — 在 Windows 上运行"""
import sys

print("=" * 60)
print("Python:", sys.version)
print("=" * 60)

# 1. 检查 easytrader
try:
    import easytrader
    print(f"easytrader 版本: {easytrader.__version__}")
    print(f"easytrader 路径: {easytrader.__file__}")
except ImportError as e:
    print(f"easytrader 未安装: {e}")
    print("请执行: pip install easytrader")
    sys.exit(1)

# 2. 列出支持的客户端类型
print(f"\n支持的客户端: {easytrader.SUPPORTED_CLIENTS if hasattr(easytrader, 'SUPPORTED_CLIENTS') else '未暴露'}")

# 3. 逐个客户端类型尝试
print("\n" + "=" * 60)
print("逐个客户端连接测试（超时各 10s）")
print("=" * 60)

for ct in ['eastmoney', 'ths', 'gj_client', 'xueqiu']:
    try:
        print(f"\n[{ct}] 尝试连接...")
        t = easytrader.use(ct)
        t.prepare(r'C:\eastmoney_trader.json')
        bal = t.balance
        print(f"[{ct}] ✓ 成功! balance={bal}")
    except Exception as e:
        print(f"[{ct}] ✗ 失败: {type(e).__name__}: {e}")

print("\n" + "=" * 60)
print("诊断完成")
print("=" * 60)
