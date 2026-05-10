# mx-search: 妙想资讯搜索 Skill

## 触发方式

用户搜索财经新闻、研报、公告、政策、事件影响分析时激活。避免 AI 引用过时或非权威金融信息。

## 可用功能

基于东方财富妙想搜索能力，金融场景智能信源筛选：

- 新闻、公告、研报、政策
- 具体事件影响分析
- 行业/板块动态

## 使用方式

```bash
cd ~/skills/mx-search && python3 mx_search.py "<查询>"
```

示例：
```bash
python3 mx_search.py "贵州茅台最新研报"
python3 mx_search.py "新能源汽车产业政策最新解读"
python3 mx_search.py "美联储加息对A股影响分析"
```

## 输出

自动保存到 `~/mx_data/output/`：纯文本结果、原始 JSON。
