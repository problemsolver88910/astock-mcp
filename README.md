# A股1分钟行情API服务

供ChatGPT通过Custom GPT Action调用的REST API服务，提供A股1分钟K线行情数据。

## 功能特性

- **股票解析**：支持名称/代码模糊匹配，自动判断市场（sh/sz）
- **1分钟K线**：获取指定日期范围的完整1分钟行情数据
- **整日概要**：开盘/收盘/最高/最低/成交量/成交额/涨跌幅/振幅
- **智能缓存**：SQLite缓存完整交易日数据，减少API调用
- **交易日历**：内置2026年A股休市日，自动跳过非交易日
- **API鉴权**：可选API密钥保护
- **OpenAPI规范**：自动生成 `/openapi.json`，方便ChatGPT理解

## 数据来源

新浪财经1分钟K线API：
- URL: `https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData`
- 最大历史深度：约1500根K线 ≈ 6个交易日（240根/天）
- 数据字段：time, open, high, low, close, volume, amount

## 项目结构

```
astock-mcp/
├── main.py              # FastAPI服务主程序
├── data_fetcher.py      # 数据获取层（新浪API）
├── cache.py             # SQLite缓存层
├── trading_calendar.py  # 交易日历
├── stock_resolver.py    # 股票名称/代码解析
├── requirements.txt     # Python依赖
├── Dockerfile           # Docker镜像构建
├── docker-compose.yml   # Docker Compose配置
├── data/                # 数据目录（SQLite缓存）
│   └── cache.db
└── README.md            # 本文件
```

## 快速开始

### 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务（开发模式，不启用API鉴权）
uvicorn main:app --host 0.0.0.0 --port 8000

# 或启用API鉴权
API_KEY=your-secret-key uvicorn main:app --host 0.0.0.0 --port 8000
```

### Docker运行

```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止
docker-compose down
```

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `API_KEY` | API访问密钥，未设置则不启用鉴权 | 空（开发模式） |
| `USE_PROXY` | 是否使用HTTP代理，0=禁用，1=启用 | 自动检测 |
| `PORT` | 服务端口 | 8000 |
| `HTTP_PROXY` | HTTP代理地址 | 系统环境变量 |
| `HTTPS_PROXY` | HTTPS代理地址 | 系统环境变量 |

## API端点

### 1. 健康检查

```
GET /api/health
```

返回服务状态、版本、时间等信息。

### 2. 股票搜索

```
GET /api/stock/search?q=西藏旅游
```

**参数：**
- `q`（必填）：股票名称或代码

**响应示例：**
```json
{
  "query": "西藏旅游",
  "result": {
    "symbol": "sh600749",
    "name": "西藏旅游",
    "type": "stock"
  }
}
```

### 3. 1分钟行情

```
GET /api/quote/minute?symbols=西藏旅游,平安银行&start_date=2026-09-02&end_date=2026-09-04
```

**参数：**
- `symbols`（必填）：逗号分隔的股票名称或代码，最多5只
- `start_date`（必填）：起始日期 YYYY-MM-DD
- `end_date`（必填）：结束日期 YYYY-MM-DD
- `start_time`（可选）：起始时间 HH:MM
- `end_time`（可选）：结束时间 HH:MM

**响应示例：**
```json
{
  "stocks": [
    {
      "symbol": "sh600749",
      "name": "西藏旅游",
      "dates": [
        {
          "date": "2026-09-04",
          "bars": [
            {
              "time": "09:30:00",
              "open": 10.50,
              "high": 10.55,
              "low": 10.48,
              "close": 10.52,
              "volume": 12345,
              "amount": 1296000,
              "prev_close": 10.45,
              "pct_change": 0.67
            }
          ],
          "bar_count": 240,
          "is_complete": true,
          "from_cache": false
        }
      ]
    }
  ],
  "total_bars": 240
}
```

### 4. 整日概要

```
GET /api/quote/summary?symbols=西藏旅游&start_date=2026-09-02&end_date=2026-09-04
```

**响应示例：**
```json
{
  "stocks": [
    {
      "symbol": "sh600749",
      "name": "西藏旅游",
      "summaries": [
        {
          "date": "2026-09-04",
          "open": 10.50,
          "close": 10.68,
          "high": 10.75,
          "low": 10.45,
          "volume": 256000,
          "amount": 27100000,
          "prev_close": 10.45,
          "pct_change": 2.20,
          "amplitude": 2.87,
          "bar_count": 240,
          "is_complete": true,
          "from_cache": true
        }
      ]
    }
  ]
}
```

### 5. 缓存状态

```
GET /api/cache/status
GET /api/cache/status?symbol=sh600749
```

## 使用建议（ChatGPT集成）

1. **先用概要定位**：调用 `/api/quote/summary` 获取多日概要，找到关键日期
2. **再用明细分析**：对关键日期调用 `/api/quote/minute` 获取1分钟数据
3. **时段筛选**：使用 `start_time`/`end_time` 参数聚焦特定时段（如开盘30分钟）
4. **缓存利用**：完整交易日数据自动缓存，重复查询速度更快

## 限制说明

- 单次请求最多 **5只股票**
- 单次请求最多 **10个交易日**
- 新浪API最大历史深度约 **6个交易日**（1500根K线）
- A股每天1分钟K线为 **240根**（9:30-11:30共120根，13:00-15:00共120根）

## 部署到云平台

### Render.com

1. Fork本项目到GitHub
2. 在Render.com创建Web Service，连接仓库
3. 设置：
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Environment Variables: `API_KEY=your-secret-key`
4. 免费层可用，但服务会休眠，建议升级到付费层

### Railway

1. 连接GitHub仓库
2. Railway自动检测Python项目
3. 设置环境变量 `API_KEY`
4. 部署完成后获取公网URL

### 自有服务器

```bash
# 使用Docker Compose
API_KEY=your-secret-key USE_PROXY=0 docker-compose up -d

# 或使用systemd管理
# 创建 /etc/systemd/system/astock-api.service
```

## 安全建议

1. **生产环境务必设置API_KEY**，防止未授权访问
2. 使用HTTPS（通过Nginx反向代理或云平台自动HTTPS）
3. 定期清理过期缓存数据
4. 监控API调用频率，防止滥用

## 许可证

MIT License
