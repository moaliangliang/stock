#!/usr/bin/env python3
"""东方财富 easytrader 诊断第三步 — 查窗口标题 (纯 Python)"""
import subprocess
import sys

print("=" * 60)
print("Python:", sys.version)
print("=" * 60)

# 1. 用 PowerShell 脚本文件方式列出窗口
print("\n[1] 搜索东方财富/同花顺/交易相关窗口...")

ps_script = r'''
$signature = @"
[DllImport("user32.dll")]
public static extern bool EnumWindows(IntPtr lpEnumFunc, IntPtr lParam);
[DllImport("user32.dll")]
public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder lpString, int nMaxCount);
[DllImport("user32.dll")]
public static extern bool IsWindowVisible(IntPtr hWnd);
[DllImport("user32.dll")]
public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
"@
$type = Add-Type -MemberDefinition $signature -Name "WinApi" -Namespace "Win32" -PassThru

$found = @()
$type::EnumWindows({
    param($hWnd, $lParam)
    $sb = New-Object System.Text.StringBuilder(512)
    [Win32.WinApi]::GetWindowText($hWnd, $sb, 512)
    $title = $sb.ToString()
    if ($title -match "东方|同花顺|交易|THS|eastmoney|财富|委托|买入|卖出|资产|持仓") {
        $pid = 0
        [Win32.WinApi]::GetWindowThreadProcessId($hWnd, [ref]$pid)
        $visible = [Win32.WinApi]::IsWindowVisible($hWnd)
        try {
            $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
            $procName = if ($proc) { $proc.ProcessName } else { "N/A" }
        } catch { $procName = "N/A" }
        $found += "PID=$pid Proc=$procName Visible=$visible Title=`"$title`""
    }
}, [IntPtr]::Zero)

if ($found.Count -eq 0) {
    Write-Output "NO_MATCH"
} else {
    $found | ForEach-Object { Write-Output $_ }
}
'''

# Write ps1 to temp file
ps1_path = "temp_winlist.ps1"
with open(ps1_path, 'w', encoding='utf-8') as f:
    f.write(ps_script)

try:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1_path],
        capture_output=True, text=True, timeout=30,
    )
    if result.stdout.strip():
        print(result.stdout)
    else:
        print("(无匹配窗口)")
    if result.stderr:
        print("STDERR:", result.stderr[:500])
except Exception as e:
    print(f"执行失败: {e}")

# Cleanup temp file
import os
try:
    os.remove(ps1_path)
except:
    pass

# 2. 列出所有窗口标题（简单版）
print("\n[2] 所有可见窗口（前50个）...")
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

titles = []
def enum_callback(hwnd, lparam):
    if user32.IsWindowVisible(hwnd):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if title:
                titles.append(title)
    return True

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows(WNDENUMPROC(enum_callback), 0)

for i, t in enumerate(titles[:50], 1):
    print(f"  [{i}] {t}")

# 3. 查 easytrader 配置
print("\n[3] easytrader ths 配置...")
import easytrader
t = easytrader.use("ths")
print(f"  broker_type: {t.broker_type}")
cfg = t.config
print(f"  config type: {type(cfg).__name__}")
# 列出 config 的属性
for attr in dir(cfg):
    if not attr.startswith("_") and not callable(getattr(cfg, attr, None)):
        val = getattr(cfg, attr)
        print(f"    {attr} = {val}")

print("\n" + "=" * 60)
print("诊断完成")
print("=" * 60)
