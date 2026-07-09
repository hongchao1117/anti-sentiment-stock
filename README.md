# anti-sentiment-stock

「别人恐惧我贪婪」—— 基于反散户情绪的量化工具有，恐慌指数越高，买入信号越强。

覆盖 **A股 + 港股 + 美股** 全市场搜索，实时行情 + 因子评分 + 买卖建议。

## 快速开始

```bash
# 1. 启动后端服务
python stock_server.py

# 2. 浏览器打开
http://localhost:8765
```

打开后直接搜索任意股票代码或名称（支持拼音缩写），即可看到恐慌买入评分。

## 项目结构

```
anti-sentiment-stock/
├── anti-retail-sentiment.html  # 前端页面（自包含，内嵌157只股票离线数据）
├── stock_server.py             # 后端API服务（8765端口）
├── gen_stocks.py               # 股票数据库生成脚本（从社优据拉取）
├── stocks_db.json              # 离线股票数据库（JSON格式）
├── embed_db.py                 # 将stocks_db.json内嵌到HTML的工具有
└── README.md
```

## 两种模式

### 模式 1：离线模式（`file://` 直打开）

直接双击 `anti-retail-sentiment.html` 打可即可用。

- 内嵌 157 只核心股票（A股/港股/美股）
- 支持拼音模糊搜索（`zhong` → 中芯国际、中国平安）
- 零网络依赖，秒开即用
- 数据通过 `python gen_stocks.py` 一键刷新

### 模式 2：在线模式（`http://localhost:8765`）

通过后端服务打开，功能最全：

- 搜任意 A股/港股/美股（通过 NeoData + 腾讯自选股实时查询）
- 实时行情、PE/PB/FB、K线涨跌幅
- 反散户情绪评分分析
- 支持代码/名称/拼音三种搜索方式

## 后端 API

| 端点 | 说明 | 示例 |
|---|---|---|
| `GET /api/health` | 健康检查 | `curl localhost:8765/api/health` |
| `GET /api/search?q=腾讯` | 全市场搜索 | `curl "localhost:8765/api/search?q=昆仑万维"` |
| `GET /api/quote?code=sz300502` | 实时行情 | `curl localhost:8765/api/quote?code=sh600519` |
| `GET /api/analyze?code=sz300502` | 分析评分 | `curl localhost:8765/api/analyze?code=sz300502` |

## 反散户恐慌指数（ARSI）

### 六大因子

| 因子 | 权重 | 信号含义 |
|---|---|---|
| 成交量萎缩 | 25分 | 散户远离市场，成交低迷 |
| 融资余额回落 | 20分 | 杠杆资金撤离，散户恐慌 |
| 市场宽度恶化 | 20分 | 多数股票下跌，赚钱效应差 |
| 指数高点回撤 | 15分 | 大盘从高点回落 |
| 媒体恐慌情绪 | 10分 | 恐慌关键词频繁出现 |
| 新增开户动能 | 10分 | 开户放缓，散户信心不足 |

### 操作信号

| 恐慌指数 | 评级 | 操作建议 |
|---|---|---|
| 76-100 | ★★★★★ | 强烈买入 —— 极度恐慌，别人恐惧我贪婪 |
| 55-75 | ★★★★ | 分批买入 —— 中度恐慌，逐步建仓 |
| 40-54 | ★★★ | 持有观望 —— 市场中性，精选个股 |
| 25-39 | ★★ | 谨慎/减仓 —— 偏贪婪，逢高减仓 |
| 0-24 | ★ | 建议回避 —— 极度贪婪，逆向卖出 |

### 个股评分（四因子）

对每只个股额外计算：回撤得分（35分）+ 估值得分（25分）+ 恐慌动量（20分）+ 大盘加成（20分）= 100分总分。

## 数据来源

- [腾讯自选股](https://gu.qq.com/) —— 实时行情、K线、财务报表
- NeoData 金融搜索 —— 全市场股票搜索
- Wind / 中证数据 —— 融资融券、成交量等市场数据

## 技术栈

前端：HTML5 + ECharts 5（图表可视化）+ 原生 JavaScript
后端：Python 3（http.server）+ Node.js（westock-data 数据接口）

## License

MIT
