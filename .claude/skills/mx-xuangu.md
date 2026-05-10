# mx-xuangu: 妙想智能选股 Skill

## 触发方式

用户输入包含"选股"/"筛选"/"条件"/"扫股"等意图时激活。基于东方财富实时数据进行筛选。

## 可用功能

按行情指标、财务指标等条件智能筛选A股股票：

- 行情筛选：涨跌幅、成交量、市盈率、市净率、股价区间
- 财务筛选：净利润增长率、ROE、股息率
- 行业/板块选股
- 指数成分股筛选
- 多条件组合

## 使用方式

执行 Python 脚本调用东方财富官方选股 API：

```bash
cd ~/skills/mx-xuangu && python3 mx_xuangu.py "<选股条件>"
```

示例：
```bash
# 行情筛选
python3 mx_xuangu.py "今日涨幅大于2%的A股"
python3 mx_xuangu.py "市盈率小于20且市净率小于2的股票"

# 财务筛选
python3 mx_xuangu.py "净利润增长率大于30%的股票"

# 行业板块
python3 mx_xuangu.py "新能源板块市盈率小于30的股票"

# 组合条件
python3 mx_xuangu.py "价格小于20元 市盈率小于20 涨幅大于1% A股"
```

## 输出

自动保存到 `~/mx_data/output/`：CSV 结果表格、原始 JSON、查询描述文件。

## 要求

- 环境变量 `MX_APIKEY` 已配置
- 网络可访问 `mkapi2.dfcfs.com`
