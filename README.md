# anti-sentiment-stock

「别人恐惧我贪婪」—— 基于反散户情绪的量化工具。恐慌指数越高，买入信号越强。

覆盖 **A股 + 港股 + 美股** 全市场搜索，实时行情 + 因子评分 + 买卖建议。

## 快速开始

```bash
cd anti-sentiment-stock
python stock_server.py
# 浏览器打开 http://localhost:8765
```

首次启动会自动从历史K线回填恐慌指数（约240天），后续每次访问实时计算并追加当日值。

## 项目结构

```
anti-sentiment-stock/
├── anti-retail-sentiment.html  # 前端页面（服务端实时注入数据，无本地缓存）
├── stock_server.py             # 后端服务（8765端口）：行情计算 + HTML渲染
├── gen_stocks.py               # 股票基础数据生成（代码/名称/行业）
├── stocks_db.json              # 股票基础数据库（代码/名称/行业，不含价格）
├── fear_history.csv            # 恐慌指数历史记录（每日自动追加）
└── README.md
```

## 使用方式

通过后端服务打开 `http://localhost:8765`：

- 实时上证指数行情（腾讯自选股）
- 六大因子拆解 + 恐慌指数仪表盘
- 历史走势图（真实K线反算，非估算）
- 全市场股票搜索（A股/港股/美股）
- 个股实时行情、PE/PB、K线涨跌幅
- 反散户情绪评分 + 买卖建议
- 支持代码/名称/拼音三种搜索

页面所有数据由服务端实时注入，无磁盘缓存。每次刷新都重新拉取最新行情。

## 后端 API

| 端点 | 说明 | 示例 |
|---|---|---|
| `GET /api/health` | 健康检查 | `curl localhost:8765/api/health` |
| `GET /api/fear-index` | 恐慌指数实时计算 | `curl localhost:8765/api/fear-index` |
| `GET /api/stocks` | 股票行情缓存 | `curl localhost:8765/api/stocks` |
| `GET /api/search?q=腾讯` | 全市场搜索 | `curl "localhost:8765/api/search?q=昆仑万维"` |
| `GET /api/quote?code=sz300502` | 实时行情 | `curl localhost:8765/api/quote?code=sh600519` |
| `GET /api/analyze?code=sz300502` | 分析评分 | `curl localhost:8765/api/analyze?code=sz300502` |

## 恐慌指数

### 计算逻辑

服务启动时从 westock-data 拉取上近一年上证指数日K线，逐日用同一套六因子公式反算，写入 `fear_history.csv`。每次页面加载计算当前实时值并追加。

公式：`恐慌指数 = 成交量(25) + 融资(20) + 宽度(20) + 回撤(15) + 媒体(10) + 开户(10)`

### 六大因子

| 因子 | 权重 | 计算依据 |
|---|---|---|
| 成交量萎缩度 | 25 | 近20日上证涨跌幅 |
| 融资余额回落 | 20 | 近60日上证趋势 |
| 市场宽度 | 20 | 当日上证涨跌 |
| 指数高点回撤 | 15 | 距52周高点回撤幅度 |
| 媒体情绪 | 10 | 当日涨跌方向 |
| 开户动能 | 10 | 固定值 5（近似） |

### 操作信号

| 恐慌指数 | 评级 | 操作建议 |
|---|---|---|
| > 70 | ★★★★★ | 强烈买入 —— 极度恐慌 |
| 50-70 | ★★★★ | 分批买入 —— 中度恐慌 |
| 25-49 | ★★ | 谨慎持有 —— 偏贪婪 |
| < 25 | ★ | 建议回避 —— 极度贪婪 |

### 个股评分

对每只个股额外计算：回撤得分（35）+ 估值（25）+ 恐慌动量（20）+ 大盘加成（20）= 100 分。

## 历史走势（fear_history.csv）

走势图数据来源为 `fear_history.csv`，每条记录都是当日真实K线反算的恐慌值，非估算。

**写入机制：**
- **启动时** — 从 westock-data 拉取过去一年上证日K线，逐日套用恐慌公式回填缺失日期（目前约 240 条）
- **每次访问首页** — 计算当前恐慌值并写入今天这一行

**与旧版对比：**
之前用噪音算法从当前值倒推，每次刷新曲线都变。现在 CSV 里 7月9日的恐慌值一旦写入就永远是 29，无论之后刷新多少次。

删掉 `fear_history.csv`，走势图会回退到"刷新一次变一次"的假数据状态。

## 数据来源

- 腾讯自选股（westock-data）—— 实时行情、日K线
- NeoData 金融搜索 —— 全市场股票搜索

## 技术栈

前端：HTML5 + ECharts 5 + 原生 JavaScript
后端：Python 3（http.server）+ Node.js（westock-data）

## License

MIT
