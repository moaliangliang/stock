#!/usr/bin/env python3
"""东方财富 easytrader 诊断第二步 — 查 API 方法"""
import subprocess
import sys

print("=" * 60)
print("Python:", sys.version)
print("=" * 60)

# 1. 查找 xiadan.exe
print("\n[1] 查找同花顺下单程序...")
try:
    result = subprocess.run(
        ["where", "/r", "C:\\", "xiadan.exe"],
        capture_output=True, text=True, timeout=30,
    )
    out = result.stdout.strip()
    if out:
        print("找到:")
        for line in out.splitlines():
            if line.strip():
                print(f"  {line.strip()}")
    else:
        print("未找到 xiadan.exe（可能在非 C 盘或非标准路径）")
except Exception as e:
    print(f"查找失败: {e}")

# 2. 列出 easytrader 方法
print("\n[2] easytrader 'ths' 客户端可用方法...")
import easytrader
t = easytrader.use("ths")
methods = [m for m in dir(t) if not m.startswith("_")]
for m in sorted(methods):
    print(f"  {m}")

# 3. 检查 connect 方法签名
print("\n[3] connect() 方法帮助...")
try:
    import inspect
    sig = inspect.signature(t.connect)
    print(f"  connect{sig}")
except Exception as e:
    print(f"  无法获取签名: {e}")

print("\n" + "=" * 60)
print("诊断完成")
print("=" * 60)
