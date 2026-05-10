# mx-moni: 妙想模拟组合管理 Skill

## 触发方式

用户操作模拟投资组合（创建/查看/清空）时激活。

## 功能

- 创建新的模拟组合
- 查看模拟组合盈亏统计
- 清空模拟组合持仓
- 获取指定组合详情

## 使用方式

```bash
cd ~/skills/mx-moni && python3 mx_moni.py "<操作>"
```

示例：
```bash
# 创建组合
python3 mx_moni.py "创建一个高股息策略组合"
# 查看列表
python3 mx_moni.py "查看组合列表"
# 获取详情
python3 mx_moni.py "获取组合详情G0001"
# 清空组合
python3 mx_moni.py "清空组合G0001"
```

## 输出

自动保存到 `~/mx_data/output/`：JSON 原始响应。
