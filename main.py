"""
A股1分钟行情API服务 - FastAPI主程序

供ChatGPT通过Custom GPT Action调用的REST API服务。

端点：
- GET /api/health          -> 服务状态
- GET /api/stock/search    -> 股票名称/代码解析
- GET /api/quote/minute    -> 获取1分钟行情
- GET /api/quote/summary   -> 获取整日概要
- GET /api/cache/status    -> 缓存状态查询

安全：
- 通过环境变量 API_KEY 设置访问密钥，请求需带 Header X-API-Key
- 如果未设置API_KEY则不启用鉴权（开发模式）

数据量限制：
- 单次请求最多5只股票，最多10个交易日
"""

import os
import sys
import logging
import datetime
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Query, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import pandas as pd

# 确保当前目录在sys.path中（本地运行时）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_calendar import (
    is_trading_day,
    get_trading_days,
    get_previous_trading_day,
    count_trading_days,
)
from stock_resolver import (
    resolve_stock,
    resolve_stocks,
    fetch_stock_name_from_sina,
    STOCK_MAP,
)
from cache import (
    init_db,
    save_to_cache,
    load_from_cache,
    is_cache_complete,
    get_cache_status,
    get_cache_stats,
    MIN_BARS_FOR_COMPLETE,
)
from data_fetcher import (
    fetch_minute_data,
    fetch_minute_data_by_date,
    fetch_daily_data,
)

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger("astock-api")

# ============================================================
# 配置
# ============================================================
API_KEY = os.environ.get("API_KEY", "").strip()
MAX_STOCKS_PER_REQUEST = 5
MAX_TRADING_DAYS_PER_REQUEST = 10

# ============================================================
# 数据模型
# ============================================================

class HealthResponse(BaseModel):
    status: str = Field(..., description="服务状态")
    version: str = Field(..., description="服务版本")
    timestamp: str = Field(..., description="当前时间")
    api_key_enabled: bool = Field(..., description="是否启用API密钥鉴权")
    max_stocks_per_request: int = Field(..., description="单次请求最大股票数")
    max_trading_days_per_request: int = Field(..., description="单次请求最大交易日数")


class StockSearchResult(BaseModel):
    symbol: str = Field(..., description="股票代码（带市场前缀，如sh600749）")
    name: str = Field(..., description="股票名称")
    type: str = Field(..., description="类型：stock/index/etf")


class StockSearchResponse(BaseModel):
    query: str = Field(..., description="原始查询")
    result: Optional[StockSearchResult] = Field(None, description="解析结果")
    message: Optional[str] = Field(None, description="提示信息")


class MinuteBar(BaseModel):
    time: str = Field(..., description="时间 HH:MM:SS")
    open: float = Field(..., description="开盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    close: float = Field(..., description="收盘价")
    volume: float = Field(..., description="成交量（手）")
    amount: float = Field(..., description="成交额（元）")
    prev_close: Optional[float] = Field(None, description="前一交易日收盘价")
    pct_change: Optional[float] = Field(None, description="涨跌幅（%），相对于prev_close")


class DailyBars(BaseModel):
    date: str = Field(..., description="日期 YYYY-MM-DD")
    bars: List[MinuteBar] = Field(..., description="当日1分钟K线列表")
    bar_count: int = Field(..., description="K线数量")
    is_complete: bool = Field(..., description="是否为完整交易日（240根）")
    from_cache: bool = Field(..., description="是否来自缓存")


class StockMinuteData(BaseModel):
    symbol: str = Field(..., description="股票代码")
    name: str = Field(..., description="股票名称")
    dates: List[DailyBars] = Field(..., description="按日期分组的K线数据")


class MinuteQuoteResponse(BaseModel):
    stocks: List[StockMinuteData] = Field(..., description="股票数据列表")
    request_params: Dict[str, Any] = Field(..., description="请求参数回显")
    total_bars: int = Field(..., description="返回K线总数")


class DailySummary(BaseModel):
    date: str = Field(..., description="日期 YYYY-MM-DD")
    open: Optional[float] = Field(None, description="开盘价")
    close: Optional[float] = Field(None, description="收盘价")
    high: Optional[float] = Field(None, description="最高价")
    low: Optional[float] = Field(None, description="最低价")
    volume: Optional[float] = Field(None, description="成交量（手）")
    amount: Optional[float] = Field(None, description="成交额（元）")
    prev_close: Optional[float] = Field(None, description="前收盘价")
    pct_change: Optional[float] = Field(None, description="涨跌幅（%）")
    amplitude: Optional[float] = Field(None, description="振幅（%）")
    bar_count: int = Field(..., description="K线数量")
    is_complete: bool = Field(..., description="是否完整交易日")
    from_cache: bool = Field(..., description="是否来自缓存")


class StockSummaryData(BaseModel):
    symbol: str = Field(..., description="股票代码")
    name: str = Field(..., description="股票名称")
    summaries: List[DailySummary] = Field(..., description="每日概要列表")


class SummaryResponse(BaseModel):
    stocks: List[StockSummaryData] = Field(..., description="股票概要数据列表")
    request_params: Dict[str, Any] = Field(..., description="请求参数回显")


class CacheStatusResponse(BaseModel):
    stats: Dict[str, Any] = Field(..., description="缓存统计信息")
    entries: List[Dict[str, Any]] = Field(..., description="缓存条目列表")


class ErrorResponse(BaseModel):
    error: str = Field(..., description="错误类型")
    message: str = Field(..., description="错误信息")
    detail: Optional[str] = Field(None, description="详细信息")


# ============================================================
# 鉴权依赖
# ============================================================

async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """
    验证API密钥。

    如果环境变量API_KEY未设置，则不启用鉴权（开发模式）。
    如果设置了API_KEY，请求必须携带正确的X-API-Key头。
    """
    if not API_KEY:
        return True  # 开发模式，不鉴权

    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少API密钥。请在请求头中提供 X-API-Key。",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API密钥无效。",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return True


# ============================================================
# 应用生命周期
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理。
    启动时初始化数据库，关闭时清理资源。
    """
    logger.info("=" * 60)
    logger.info("A股1分钟行情API服务启动中...")
    logger.info(f"API密钥鉴权: {'已启用' if API_KEY else '未启用（开发模式）'}")
    logger.info(f"单次请求限制: 最多{MAX_STOCKS_PER_REQUEST}只股票, 最多{MAX_TRADING_DAYS_PER_REQUEST}个交易日")

    # 初始化数据库
    init_db()
    logger.info("数据库初始化完成")

    logger.info("服务启动完成")
    logger.info("=" * 60)

    yield

    logger.info("服务关闭中...")


app = FastAPI(
    title="A股1分钟行情API服务",
    description=(
        "提供A股1分钟K线行情数据的REST API服务，供ChatGPT通过Custom GPT Action调用。\n\n"
        "## 功能\n"
        "- 股票名称/代码解析\n"
        "- 1分钟K线数据查询（支持多股票、日期范围、时间范围筛选）\n"
        "- 整日概要数据查询（开盘/收盘/最高/最低/成交量/成交额/涨跌幅/振幅）\n"
        "- SQLite缓存（完整交易日数据自动缓存）\n\n"
        "## 数据来源\n"
        "新浪财经1分钟K线API，最大历史深度约6个交易日（1500根K线）。\n\n"
        "## 限制\n"
        f"- 单次请求最多 {MAX_STOCKS_PER_REQUEST} 只股票\n"
        f"- 单次请求最多 {MAX_TRADING_DAYS_PER_REQUEST} 个交易日\n\n"
        "## 鉴权\n"
        "通过环境变量 API_KEY 设置访问密钥，请求需带 Header X-API-Key。"
        "未设置时不启用鉴权（开发模式）。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS中间件（允许ChatGPT等外部调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 工具函数
# ============================================================

def parse_date(date_str: str) -> datetime.date:
    """
    解析日期字符串，支持 YYYY-MM-DD 格式。

    Args:
        date_str: 日期字符串

    Returns:
        datetime.date对象

    Raises:
        HTTPException: 日期格式无效
    """
    try:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"日期格式无效: '{date_str}'，请使用 YYYY-MM-DD 格式"
        )


def parse_time(time_str: str) -> str:
    """
    解析时间字符串，规范化为 HH:MM:SS 格式。

    Args:
        time_str: 时间字符串（HH:MM 或 HH:MM:SS）

    Returns:
        规范化后的时间字符串
    """
    try:
        if len(time_str) == 5:  # HH:MM
            t = datetime.datetime.strptime(time_str, "%H:%M").time()
        else:
            t = datetime.datetime.strptime(time_str, "%H:%M:%S").time()
        return t.strftime("%H:%M:%S")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"时间格式无效: '{time_str}'，请使用 HH:MM 或 HH:MM:SS 格式"
        )


def validate_request_limits(symbols: List[str], start_date: datetime.date, end_date: datetime.date):
    """
    验证请求限制：股票数量和交易日数量。

    Args:
        symbols: 股票代码列表
        start_date: 起始日期
        end_date: 结束日期

    Raises:
        HTTPException: 超出限制
    """
    if len(symbols) > MAX_STOCKS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"单次请求最多支持 {MAX_STOCKS_PER_REQUEST} 只股票，当前请求 {len(symbols)} 只"
        )

    trading_days = get_trading_days(start_date, end_date)
    if len(trading_days) > MAX_TRADING_DAYS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"单次请求最多支持 {MAX_TRADING_DAYS_PER_REQUEST} 个交易日，当前请求 {len(trading_days)} 个交易日"
        )

    if not trading_days:
        raise HTTPException(
            status_code=400,
            detail=f"日期范围内没有交易日: {start_date} ~ {end_date}"
        )


def df_to_minute_bars(df: pd.DataFrame, start_time: Optional[str] = None, end_time: Optional[str] = None) -> List[MinuteBar]:
    """
    将DataFrame转换为MinuteBar列表，并计算pct_change。

    Args:
        df: 包含1分钟K线数据的DataFrame
        start_time: 可选，起始时间筛选
        end_time: 可选，结束时间筛选

    Returns:
        MinuteBar列表
    """
    bars = []

    # 时间筛选
    if start_time:
        df = df[df['time'] >= start_time]
    if end_time:
        df = df[df['time'] <= end_time]

    for _, row in df.iterrows():
        prev_close = row.get('prev_close')
        close = row.get('close', 0)

        pct_change = None
        if prev_close and prev_close > 0 and close:
            pct_change = round((close - prev_close) / prev_close * 100, 4)

        bar = MinuteBar(
            time=str(row.get('time', '')),
            open=float(row.get('open', 0) or 0),
            high=float(row.get('high', 0) or 0),
            low=float(row.get('low', 0) or 0),
            close=float(close or 0),
            volume=float(row.get('volume', 0) or 0),
            amount=float(row.get('amount', 0) or 0),
            prev_close=float(prev_close) if prev_close and pd.notna(prev_close) else None,
            pct_change=pct_change,
        )
        bars.append(bar)

    return bars


def get_stock_minute_data_with_cache(
    symbol: str,
    name: str,
    start_date: datetime.date,
    end_date: datetime.date,
) -> StockMinuteData:
    """
    获取单只股票的1分钟数据，优先使用缓存。

    流程：
    1. 检查缓存是否完整覆盖日期范围
    2. 如果缓存完整，直接从缓存加载
    3. 如果缓存不完整，从数据源获取并保存到缓存
    4. 按日期分组返回

    Args:
        symbol: 股票代码
        name: 股票名称
        start_date: 起始日期
        end_date: 结束日期

    Returns:
        StockMinuteData对象
    """
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    trading_days = get_trading_days(start_date, end_date)

    daily_bars_list = []
    total_bars = 0

    for trade_date in trading_days:
        date_str = trade_date.strftime('%Y-%m-%d')
        from_cache = False
        df_day = None

        # 1. 检查缓存
        if is_cache_complete(symbol, date_str):
            cached_df = load_from_cache(symbol, date_str, date_str)
            if cached_df is not None and not cached_df.empty:
                df_day = cached_df
                from_cache = True
                logger.info(f"缓存命中: {symbol} {date_str}, {len(df_day)} 根")

        # 2. 缓存不完整或未命中，从数据源获取
        if df_day is None:
            logger.info(f"从数据源获取: {symbol} {date_str}")
            df_day = fetch_minute_data_by_date(symbol, trade_date)

            if df_day is not None and not df_day.empty:
                # 保存到缓存
                save_to_cache(symbol, date_str, df_day)
            else:
                logger.warning(f"无法获取数据: {symbol} {date_str}")
                continue

        # 3. 转换为MinuteBar列表
        bars = df_to_minute_bars(df_day)
        bar_count = len(bars)
        is_complete = bar_count >= MIN_BARS_FOR_COMPLETE

        daily_bars = DailyBars(
            date=date_str,
            bars=bars,
            bar_count=bar_count,
            is_complete=is_complete,
            from_cache=from_cache,
        )
        daily_bars_list.append(daily_bars)
        total_bars += bar_count

    return StockMinuteData(
        symbol=symbol,
        name=name,
        dates=daily_bars_list,
    )


def calculate_daily_summary(df: pd.DataFrame, date_str: str, from_cache: bool) -> DailySummary:
    """
    从1分钟数据计算整日概要。

    Args:
        df: 当日1分钟K线数据
        date_str: 日期
        from_cache: 是否来自缓存

    Returns:
        DailySummary对象
    """
    if df is None or df.empty:
        return DailySummary(
            date=date_str,
            bar_count=0,
            is_complete=False,
            from_cache=from_cache,
        )

    open_price = float(df.iloc[0]['open']) if len(df) > 0 else None
    close_price = float(df.iloc[-1]['close']) if len(df) > 0 else None
    high_price = float(df['high'].max())
    low_price = float(df['low'].min())
    volume = float(df['volume'].sum())
    amount = float(df['amount'].sum())

    prev_close = None
    if 'prev_close' in df.columns:
        pc_values = df['prev_close'].dropna()
        if not pc_values.empty:
            prev_close = float(pc_values.iloc[0])

    pct_change = None
    if prev_close and prev_close > 0 and close_price:
        pct_change = round((close_price - prev_close) / prev_close * 100, 4)

    amplitude = None
    if prev_close and prev_close > 0 and high_price and low_price:
        amplitude = round((high_price - low_price) / prev_close * 100, 4)

    bar_count = len(df)
    is_complete = bar_count >= MIN_BARS_FOR_COMPLETE

    return DailySummary(
        date=date_str,
        open=open_price,
        close=close_price,
        high=high_price,
        low=low_price,
        volume=volume,
        amount=amount,
        prev_close=prev_close,
        pct_change=pct_change,
        amplitude=amplitude,
        bar_count=bar_count,
        is_complete=is_complete,
        from_cache=from_cache,
    )


# ============================================================
# API端点
# ============================================================

@app.get(
    "/api/health",
    response_model=HealthResponse,
    summary="服务健康检查",
    description="检查API服务是否正常运行，返回服务状态、版本、时间等信息。",
    tags=["系统"],
)
async def health_check():
    """
    服务健康检查端点。
    不需要API密钥鉴权。
    """
    return HealthResponse(
        status="ok",
        version="1.0.0",
        timestamp=datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        api_key_enabled=bool(API_KEY),
        max_stocks_per_request=MAX_STOCKS_PER_REQUEST,
        max_trading_days_per_request=MAX_TRADING_DAYS_PER_REQUEST,
    )


@app.get(
    "/api/stock/search",
    response_model=StockSearchResponse,
    summary="股票名称/代码解析",
    description=(
        "将股票名称或代码解析为标准格式。\n\n"
        "支持以下输入：\n"
        "- 纯数字代码：如 600749, 000001\n"
        "- 带市场前缀代码：如 sh600749, sz000001\n"
        "- 股票名称：如 西藏旅游, 平安银行\n"
        "- 指数名称：如 上证指数, 沪深300\n"
        "- ETF名称：如 沪深300ETF\n\n"
        "代码自动判断市场：6开头→sh，0/3开头→sz"
    ),
    tags=["股票查询"],
    dependencies=[Depends(verify_api_key)],
)
async def search_stock(
    q: str = Query(..., description="股票名称或代码，如：西藏旅游、600749、sh600749、平安银行"),
):
    """
    股票搜索/解析端点。
    """
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="查询参数 q 不能为空")

    result = resolve_stock(q)

    if result:
        return StockSearchResponse(
            query=q,
            result=StockSearchResult(**result),
        )

    # 内置映射未找到，尝试通过新浪API动态查询（仅对纯数字代码）
    import re
    if re.match(r'^\d{6}$', q.strip()):
        from stock_resolver import _infer_market
        market = _infer_market(q.strip())
        symbol = f"{market}{q.strip()}"
        name = fetch_stock_name_from_sina(symbol)
        if name:
            return StockSearchResponse(
                query=q,
                result=StockSearchResult(symbol=symbol, name=name, type="stock"),
                message="通过新浪API动态查询到股票名称",
            )

    return StockSearchResponse(
        query=q,
        result=None,
        message=f"未找到匹配的股票: '{q}'。请检查名称或代码是否正确。",
    )


@app.get(
    "/api/quote/minute",
    response_model=MinuteQuoteResponse,
    summary="获取1分钟行情数据",
    description=(
        "获取指定股票在指定日期范围内的1分钟K线数据。\n\n"
        "## 参数说明\n"
        "- symbols: 逗号分隔的股票名称或代码，最多5只\n"
        "- start_date: 起始日期 YYYY-MM-DD\n"
        "- end_date: 结束日期 YYYY-MM-DD\n"
        "- start_time: 可选，起始时间 HH:MM，用于筛选日内时段\n"
        "- end_time: 可选，结束时间 HH:MM，用于筛选日内时段\n\n"
        "## 数据说明\n"
        "- 每根K线包含：time, open, high, low, close, volume, amount, prev_close, pct_change\n"
        "- pct_change为相对于前一交易日收盘价的涨跌幅（%）\n"
        "- 完整交易日为240根K线（上午9:30-11:30共120根，下午13:00-15:00共120根）\n"
        "- 数据自动缓存，完整交易日数据第二次查询从缓存读取\n\n"
        "## 限制\n"
        "- 最多5只股票，最多10个交易日\n"
        "- 新浪API最大历史深度约6个交易日"
    ),
    tags=["行情数据"],
    dependencies=[Depends(verify_api_key)],
)
async def get_minute_quote(
    symbols: str = Query(..., description="逗号分隔的股票名称或代码，如：西藏旅游,平安银行,sh600749"),
    start_date: str = Query(..., description="起始日期 YYYY-MM-DD，如：2026-09-02"),
    end_date: str = Query(..., description="结束日期 YYYY-MM-DD，如：2026-09-04"),
    start_time: Optional[str] = Query(None, description="可选，起始时间 HH:MM，如：09:30"),
    end_time: Optional[str] = Query(None, description="可选，结束时间 HH:MM，如：11:30"),
):
    """
    获取1分钟行情数据端点。
    """
    # 解析日期
    s_date = parse_date(start_date)
    e_date = parse_date(end_date)

    if s_date > e_date:
        raise HTTPException(status_code=400, detail="起始日期不能大于结束日期")

    # 解析时间
    s_time = parse_time(start_time) if start_time else None
    e_time = parse_time(end_time) if end_time else None

    # 解析股票
    symbol_list = [s.strip() for s in symbols.split(',') if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="symbols参数不能为空")

    # 验证请求限制
    validate_request_limits(symbol_list, s_date, e_date)

    # 解析每只股票
    resolved_stocks = []
    for q in symbol_list:
        result = resolve_stock(q)
        if result is None:
            raise HTTPException(
                status_code=400,
                detail=f"无法解析股票: '{q}'。请使用 /api/stock/search 验证股票名称或代码。"
            )
        resolved_stocks.append(result)

    # 去重（按symbol）
    seen = set()
    unique_stocks = []
    for s in resolved_stocks:
        if s['symbol'] not in seen:
            seen.add(s['symbol'])
            unique_stocks.append(s)

    # 获取每只股票的数据
    stock_data_list = []
    total_bars = 0

    for stock in unique_stocks:
        try:
            stock_data = get_stock_minute_data_with_cache(
                symbol=stock['symbol'],
                name=stock['name'],
                start_date=s_date,
                end_date=e_date,
            )

            # 应用时间筛选
            if s_time or e_time:
                for daily in stock_data.dates:
                    filtered_bars = []
                    for bar in daily.bars:
                        if s_time and bar.time < s_time:
                            continue
                        if e_time and bar.time > e_time:
                            continue
                        filtered_bars.append(bar)
                    daily.bars = filtered_bars
                    daily.bar_count = len(filtered_bars)

            # 统计总K线数
            for daily in stock_data.dates:
                total_bars += daily.bar_count

            stock_data_list.append(stock_data)

        except Exception as e:
            logger.error(f"获取股票数据失败 {stock['symbol']}: {e}", exc_info=True)
            # 继续处理其他股票
            continue

    return MinuteQuoteResponse(
        stocks=stock_data_list,
        request_params={
            "symbols": symbols,
            "start_date": start_date,
            "end_date": end_date,
            "start_time": start_time,
            "end_time": end_time,
        },
        total_bars=total_bars,
    )


@app.get(
    "/api/quote/summary",
    response_model=SummaryResponse,
    summary="获取整日概要数据",
    description=(
        "获取指定股票在指定日期范围内的每日概要数据。\n\n"
        "## 概要指标\n"
        "- open: 开盘价\n"
        "- close: 收盘价\n"
        "- high: 最高价\n"
        "- low: 最低价\n"
        "- volume: 成交量（手）\n"
        "- amount: 成交额（元）\n"
        "- prev_close: 前收盘价\n"
        "- pct_change: 涨跌幅（%）\n"
        "- amplitude: 振幅（%）\n\n"
        "## 使用建议\n"
        "ChatGPT可先用此端点定位关键时段，再用 /api/quote/minute + 时间范围获取详细数据。\n\n"
        "## 限制\n"
        "- 最多5只股票，最多10个交易日"
    ),
    tags=["行情数据"],
    dependencies=[Depends(verify_api_key)],
)
async def get_summary(
    symbols: str = Query(..., description="逗号分隔的股票名称或代码"),
    start_date: str = Query(..., description="起始日期 YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期 YYYY-MM-DD"),
):
    """
    获取整日概要数据端点。
    """
    # 解析日期
    s_date = parse_date(start_date)
    e_date = parse_date(end_date)

    if s_date > e_date:
        raise HTTPException(status_code=400, detail="起始日期不能大于结束日期")

    # 解析股票
    symbol_list = [s.strip() for s in symbols.split(',') if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="symbols参数不能为空")

    # 验证请求限制
    validate_request_limits(symbol_list, s_date, e_date)

    # 解析每只股票
    resolved_stocks = []
    for q in symbol_list:
        result = resolve_stock(q)
        if result is None:
            raise HTTPException(
                status_code=400,
                detail=f"无法解析股票: '{q}'。请使用 /api/stock/search 验证股票名称或代码。"
            )
        resolved_stocks.append(result)

    # 去重
    seen = set()
    unique_stocks = []
    for s in resolved_stocks:
        if s['symbol'] not in seen:
            seen.add(s['symbol'])
            unique_stocks.append(s)

    trading_days = get_trading_days(s_date, e_date)
    stock_summary_list = []

    for stock in unique_stocks:
        summaries = []
        symbol = stock['symbol']
        name = stock['name']

        for trade_date in trading_days:
            date_str = trade_date.strftime('%Y-%m-%d')
            from_cache = False
            df_day = None

            # 检查缓存
            if is_cache_complete(symbol, date_str):
                cached_df = load_from_cache(symbol, date_str, date_str)
                if cached_df is not None and not cached_df.empty:
                    df_day = cached_df
                    from_cache = True

            # 从数据源获取
            if df_day is None:
                df_day = fetch_minute_data_by_date(symbol, trade_date)
                if df_day is not None and not df_day.empty:
                    save_to_cache(symbol, date_str, df_day)

            # 计算概要
            summary = calculate_daily_summary(df_day, date_str, from_cache)
            summaries.append(summary)

        stock_summary_list.append(StockSummaryData(
            symbol=symbol,
            name=name,
            summaries=summaries,
        ))

    return SummaryResponse(
        stocks=stock_summary_list,
        request_params={
            "symbols": symbols,
            "start_date": start_date,
            "end_date": end_date,
        },
    )


@app.get(
    "/api/cache/status",
    response_model=CacheStatusResponse,
    summary="缓存状态查询",
    description=(
        "查询SQLite缓存的状态和统计信息。\n\n"
        "## 返回信息\n"
        "- stats: 缓存统计（总条目数、完整缓存数、不完整缓存数、覆盖股票数、总K线数）\n"
        "- entries: 缓存条目列表（symbol, date, is_complete, bar_count, fetched_at）\n\n"
        "## 可选参数\n"
        "- symbol: 指定股票代码，只查询该股票的缓存状态"
    ),
    tags=["系统"],
    dependencies=[Depends(verify_api_key)],
)
async def cache_status(
    symbol: Optional[str] = Query(None, description="可选，指定股票代码，如 sh600749"),
):
    """
    缓存状态查询端点。
    """
    stats = get_cache_stats()
    entries = get_cache_status(symbol)
    return CacheStatusResponse(stats=stats, entries=entries)


# ============================================================
# 异常处理
# ============================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """
    统一HTTP异常处理，返回JSON格式错误。
    """
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.status_code,
            "message": str(exc.detail),
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """
    通用异常处理，捕获未预期的错误。
    """
    logger.error(f"未预期的错误: {exc}", exc_info=True)
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "服务器内部错误，请稍后重试。",
            "detail": str(exc) if os.environ.get("DEBUG") else None,
        },
    )


# ============================================================
# 启动入口
# ============================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )
