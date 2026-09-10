"""
数据获取层

从新浪财经API获取A股1分钟K线数据和日线数据。

新浪1分钟K线API:
  URL: https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData
  参数: symbol(如sh600749/sz000001), scale=1, ma=no, datalen(最大约1500)
  返回: JSON数组，每项含 day(时间), open, high, low, close, volume, amount

新浪日线API:
  URL: https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData
  参数: symbol, scale=240, ma=no, datalen=10

注意：
- 新浪API只返回最近N根K线，datalen最大约1500
- 最大历史深度约1500根K线 ≈ 6个交易日（240根/天）
- 代理通过环境变量 HTTP_PROXY/HTTPS_PROXY 自动配置
- 生产部署（用户云服务器）不需要代理，通过环境变量控制
"""

import os
import time
import logging
import datetime
from typing import Optional, List, Dict, Any

import pandas as pd
import requests

from trading_calendar import (
    is_trading_day,
    get_trading_days,
    get_previous_trading_day,
)

logger = logging.getLogger(__name__)

# 新浪API基础URL
SINA_KLINE_URL = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"

# 新浪API最大datalen
MAX_DATALEN = 1500

# A股每天1分钟K线数量
BARS_PER_DAY = 240

# 重试配置
MAX_RETRIES = 3
RETRY_INTERVAL = 2  # 秒

# 请求超时
REQUEST_TIMEOUT = 15


def _get_proxies() -> Optional[Dict[str, str]]:
    """
    获取代理配置。

    生产环境（用户云服务器）通常不需要代理。
    通过环境变量 USE_PROXY 控制：
    - USE_PROXY=1 或未设置且检测到沙箱环境 → 使用代理
    - USE_PROXY=0 → 不使用代理

    requests库会自动读取 HTTP_PROXY/HTTPS_PROXY 环境变量，
    但显式传入proxies参数更可控。
    """
    use_proxy = os.environ.get("USE_PROXY", "").strip().lower()

    if use_proxy == "0" or use_proxy == "false" or use_proxy == "no":
        logger.debug("代理已禁用 (USE_PROXY=0)")
        return None

    http_proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")

    if http_proxy or https_proxy:
        proxies = {}
        if http_proxy:
            proxies["http"] = http_proxy
        if https_proxy:
            proxies["https"] = https_proxy
        logger.debug(f"使用代理: {proxies}")
        return proxies

    return None


def _request_with_retry(url: str, params: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Optional[Any]:
    """
    带重试机制的HTTP请求。

    网络失败时重试3次，间隔2秒。

    Args:
        url: 请求URL
        params: 查询参数
        headers: 请求头

    Returns:
        解析后的JSON数据，失败返回None
    """
    proxies = _get_proxies()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                params=params,
                headers=headers,
                proxies=proxies,
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()

            # 新浪API可能返回null或空
            text = resp.text.strip()
            if not text or text == "null":
                logger.warning(f"新浪API返回空数据 (attempt {attempt}/{MAX_RETRIES}): {url} {params}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_INTERVAL)
                    continue
                return None

            data = resp.json()
            return data

        except requests.exceptions.ProxyError as e:
            logger.error(f"代理错误 (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_INTERVAL)
                continue
        except requests.exceptions.Timeout as e:
            logger.error(f"请求超时 (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_INTERVAL)
                continue
        except requests.exceptions.RequestException as e:
            logger.error(f"请求失败 (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_INTERVAL)
                continue
        except (ValueError, Exception) as e:
            logger.error(f"响应解析失败 (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_INTERVAL)
                continue

    logger.error(f"请求最终失败: {url} {params}")
    return None


def fetch_minute_data_raw(symbol: str, datalen: int = 1500) -> Optional[pd.DataFrame]:
    """
    从新浪API获取最近N根1分钟K线数据（原始数据）。

    Args:
        symbol: 股票代码（如 sh600749）
        datalen: 获取的K线数量，最大约1500

    Returns:
        包含原始K线数据的DataFrame，失败返回None
        列: day(时间字符串), open, high, low, close, volume, amount
    """
    if datalen > MAX_DATALEN:
        datalen = MAX_DATALEN
        logger.warning(f"datalen超过最大值{MAX_DATALEN}，已截断")

    params = {
        "symbol": symbol,
        "scale": 1,
        "ma": "no",
        "datalen": datalen,
    }

    data = _request_with_retry(SINA_KLINE_URL, params)

    if data is None:
        return None

    if not isinstance(data, list) or len(data) == 0:
        logger.warning(f"新浪API返回数据格式异常或为空: {symbol}")
        return None

    df = pd.DataFrame(data)
    # 确保列名正确
    required_cols = ['day', 'open', 'high', 'low', 'close', 'volume']
    for col in required_cols:
        if col not in df.columns:
            logger.warning(f"返回数据缺少列 {col}: {symbol}")
            return None

    # amount列可能不存在
    if 'amount' not in df.columns:
        df['amount'] = 0.0

    # 转换数值类型
    for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    logger.info(f"获取原始1分钟数据: {symbol}, 共 {len(df)} 根K线")
    return df


def fetch_daily_data(symbol: str, datalen: int = 10) -> Optional[pd.DataFrame]:
    """
    从新浪API获取日线数据（用于获取昨收价）。

    Args:
        symbol: 股票代码（如 sh600749）
        datalen: 获取的日线数量

    Returns:
        包含日线数据的DataFrame，失败返回None
        列: day(日期), open, high, low, close, volume, amount
    """
    params = {
        "symbol": symbol,
        "scale": 240,
        "ma": "no",
        "datalen": datalen,
    }

    data = _request_with_retry(SINA_KLINE_URL, params)

    if data is None:
        return None

    if not isinstance(data, list) or len(data) == 0:
        logger.warning(f"新浪日线API返回数据为空: {symbol}")
        return None

    df = pd.DataFrame(data)
    required_cols = ['day', 'open', 'high', 'low', 'close', 'volume']
    for col in required_cols:
        if col not in df.columns:
            logger.warning(f"日线数据缺少列 {col}: {symbol}")
            return None

    if 'amount' not in df.columns:
        df['amount'] = 0.0

    for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # day格式可能是 "2026-09-04" 或 "2026-09-04 15:00:00"
    df['date'] = df['day'].astype(str).str[:10]

    logger.info(f"获取日线数据: {symbol}, 共 {len(df)} 天")
    return df


def _extract_date_from_day(day_str: str) -> str:
    """
    从新浪API的day字段提取日期。

    day格式: "2026-09-04 09:30:00" 或 "2026-09-04"

    Args:
        day_str: 时间字符串

    Returns:
        日期字符串 YYYY-MM-DD
    """
    return str(day_str)[:10]


def _extract_time_from_day(day_str: str) -> str:
    """
    从新浪API的day字段提取时间。

    Args:
        day_str: 时间字符串

    Returns:
        时间字符串 HH:MM:SS 或 HH:MM
    """
    s = str(day_str)
    if ' ' in s:
        return s.split(' ')[1]
    return s


def _calculate_datalen_for_range(start_date: datetime.date, end_date: datetime.date) -> int:
    """
    计算覆盖指定日期范围所需的datalen。

    由于新浪API只返回最近N根K线，需要估算从end_date到当前的K线数量，
    再加上请求范围内的K线数量。

    简化策略：
    - 计算请求范围内的交易日数量 × 240
    - 加上从end_date到今天的交易日数量 × 240（缓冲）
    - 最大不超过1500

    Args:
        start_date: 起始日期
        end_date: 结束日期

    Returns:
        建议的datalen值
    """
    today = datetime.date.today()

    # 请求范围内的交易日
    trading_days_in_range = get_trading_days(start_date, end_date)
    bars_in_range = len(trading_days_in_range) * BARS_PER_DAY

    # 从end_date到今天的交易日（缓冲，确保能覆盖到end_date）
    if end_date < today:
        buffer_days = get_trading_days(end_date, today)
        bars_buffer = len(buffer_days) * BARS_PER_DAY
    else:
        bars_buffer = BARS_PER_DAY  # 至少1天缓冲

    total_bars = bars_in_range + bars_buffer

    # 限制在最大值内
    datalen = min(total_bars, MAX_DATALEN)
    logger.info(
        f"计算datalen: range={start_date}~{end_date}, "
        f"trading_days={len(trading_days_in_range)}, "
        f"estimated_bars={total_bars}, datalen={datalen}"
    )
    return datalen


def fetch_minute_data(
    symbol: str,
    start_date: datetime.date,
    end_date: datetime.date,
) -> Optional[pd.DataFrame]:
    """
    获取指定日期范围的1分钟行情数据。

    流程：
    1. 检查日期范围是否超出新浪历史深度（>6个交易日）
    2. 计算所需datalen
    3. 从新浪API获取原始数据
    4. 筛选日期范围内的数据
    5. 添加prev_close（前一交易日收盘价）
    6. 按日期分组返回

    Args:
        symbol: 股票代码（如 sh600749）
        start_date: 起始日期
        end_date: 结束日期

    Returns:
        包含1分钟K线数据的DataFrame
        列: date, time, open, high, low, close, volume, amount, prev_close
        失败或超出历史深度返回None
    """
    # 检查日期范围
    if start_date > end_date:
        logger.error(f"起始日期大于结束日期: {start_date} > {end_date}")
        return None

    trading_days = get_trading_days(start_date, end_date)
    if not trading_days:
        logger.warning(f"日期范围内没有交易日: {start_date} ~ {end_date}")
        return None

    # 检查是否超出新浪历史深度（约6个交易日）
    # 注意：如果end_date是最近的交易日，6天内可以获取
    # 如果end_date较早，可能无法获取
    today = datetime.date.today()
    days_from_today = len(get_trading_days(end_date, today))

    if days_from_today > 7:
        logger.error(
            f"请求日期超出新浪历史深度: end_date={end_date}, "
            f"距今天 {days_from_today} 个交易日（最大约6-7个）"
        )
        return None

    if len(trading_days) > 10:
        logger.error(f"请求交易日数量过多: {len(trading_days)} 天（最大10天）")
        return None

    # 计算datalen
    datalen = _calculate_datalen_for_range(start_date, end_date)

    # 获取原始数据
    raw_df = fetch_minute_data_raw(symbol, datalen)
    if raw_df is None or raw_df.empty:
        return None

    # 解析日期和时间
    raw_df['date'] = raw_df['day'].apply(_extract_date_from_day)
    raw_df['time'] = raw_df['day'].apply(_extract_time_from_day)

    # 筛选日期范围内的数据
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    mask = (raw_df['date'] >= start_str) & (raw_df['date'] <= end_str)
    df = raw_df[mask].copy()

    if df.empty:
        logger.warning(f"筛选后数据为空: {symbol} {start_str} ~ {end_str}")
        return None

    # 按日期和时间排序
    df = df.sort_values(['date', 'time']).reset_index(drop=True)

    # 添加prev_close
    df = _add_prev_close(symbol, df, raw_df)

    # 选择输出列
    output_cols = ['date', 'time', 'open', 'high', 'low', 'close', 'volume', 'amount', 'prev_close']
    df = df[output_cols]

    logger.info(
        f"获取1分钟数据完成: {symbol} {start_str}~{end_str}, "
        f"共 {len(df)} 根K线, 覆盖 {df['date'].nunique()} 个交易日"
    )
    return df


def _add_prev_close(symbol: str, df: pd.DataFrame, raw_df: pd.DataFrame) -> pd.DataFrame:
    """
    为每根K线添加prev_close（前一交易日收盘价）。

    策略：
    1. 优先从raw_df中查找前一交易日的最后一根K线的close
    2. 如果raw_df中没有前一交易日数据，从日线API获取
    3. 同一天内所有K线的prev_close相同

    Args:
        symbol: 股票代码
        df: 需要添加prev_close的DataFrame（已筛选日期范围）
        raw_df: 从新浪获取的原始数据（包含更多历史数据）

    Returns:
        添加了prev_close列的DataFrame
    """
    df = df.copy()
    df['prev_close'] = None

    # 获取df中所有唯一日期
    dates = sorted(df['date'].unique())

    for date_str in dates:
        try:
            current_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            continue

        prev_date = get_previous_trading_day(current_date)
        prev_date_str = prev_date.strftime('%Y-%m-%d')

        prev_close = None

        # 策略1：从raw_df中查找前一交易日的最后一根K线
        prev_day_data = raw_df[raw_df['date'] == prev_date_str]
        if not prev_day_data.empty:
            prev_close = float(prev_day_data.iloc[-1]['close'])
            logger.debug(f"从raw_df获取prev_close: {symbol} {date_str} <- {prev_date_str} = {prev_close}")

        # 策略2：从日线API获取
        if prev_close is None:
            daily_df = fetch_daily_data(symbol, datalen=15)
            if daily_df is not None and not daily_df.empty:
                prev_daily = daily_df[daily_df['date'] == prev_date_str]
                if not prev_daily.empty:
                    prev_close = float(prev_daily.iloc[0]['close'])
                    logger.debug(f"从日线API获取prev_close: {symbol} {date_str} <- {prev_date_str} = {prev_close}")

        # 策略3：如果前一交易日也在df中，取该日最后一根close
        if prev_close is None and prev_date_str in dates:
            prev_in_df = df[df['date'] == prev_date_str]
            if not prev_in_df.empty:
                prev_close = float(prev_in_df.iloc[-1]['close'])
                logger.debug(f"从df自身获取prev_close: {symbol} {date_str} <- {prev_date_str} = {prev_close}")

        if prev_close is not None:
            df.loc[df['date'] == date_str, 'prev_close'] = prev_close
        else:
            logger.warning(f"无法获取prev_close: {symbol} {date_str} (前一交易日 {prev_date_str})")

    return df


def fetch_minute_data_by_date(symbol: str, date: datetime.date) -> Optional[pd.DataFrame]:
    """
    获取单个交易日的1分钟行情数据。

    Args:
        symbol: 股票代码
        date: 交易日日期

    Returns:
        包含1分钟K线数据的DataFrame
    """
    return fetch_minute_data(symbol, date, date)
