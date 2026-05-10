# mx-zixuan: 妙想自选股管理 Skill

## 触发方式

用户操作自选股（查询/添加/删除）时激活。

## 功能

- 查询我的自选股列表
- 添加指定股票到自选股
- 从自选股中删除指定股票

## 使用方式

```bash
cd ~/skills/mx-zixuan && python3 mx_zixuan.py "<操作>"
```

示例：
```bash
# 查询
python3 mx_zixuan.py "查询我的自选股列表"
# 添加
python3 mx_zixuan.py add "300059"
# 删除
python3 mx_zixuan.py delete "贵州茅台"
```

## 输出

自动保存到 `~/mx_data/output/`：CSV 自选股列表、原始 JSON。
