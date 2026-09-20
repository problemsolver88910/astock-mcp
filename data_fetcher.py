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

# 东方财富历史1分钟K线API（fallback数据源）
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

# 请求超时
REQUEST_TIMEOUT = 15

# 腾讯财经历史分钟K线API（第二个fallback）
TENCENT_KLINE_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def fetch_minute_data_tencent(
    symbol: str,
    start_date: datetime.date,
    end_date: datetime.date,
) -> Optional[pd.DataFrame]:
    """
    从腾讯财经API获取历史1分钟K线数据（第二个fallback）。

    返回格式: [datetime, open, close, high, low, volume]
    注意: 腾讯不直接提供amount，用 volume*close 估算。
    """
    beg = (start_date - datetime.timedelta(days=40)).strftime('%Y-%m-%d')
    end = end_date.strftime('%Y-%m-%d')
    # 每交易日240根，最多10天=2400，取上限3200
    params = {
        "param": f"{symbol},m1,{beg},{end},3200,qfq",
    }

    proxies = _get_proxies()
    try:
        resp = requests.get(
            TENCENT_KLINE_URL,
            params=params,
            proxies=proxies,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"腾讯API请求失败: {symbol} {e}")
        return None

    if not data or data.get("code") != 0:
        logger.warning(f"腾讯API返回错误: {symbol}")
        return None

    stock_data = data.get("data", {}).get(symbol, {})
    klines = stock_data.get("m1") or stock_data.get("qfqm1")
    if not klines:
        logger.warning(f"腾讯API无分钟数据: {symbol} {start_date}~{end_date}")
        return None

    rows = []
    for item in klines:
        # item: [datetime, open, close, high, low, volume]
        if len(item) < 6:
            continue
        dt_str = item[0]       # "2026-09-03 09:31"
        open_price = float(item[1])
        close_price = float(item[2])
        high_price = float(item[3])
        low_price = float(item[4])
        volume = float(item[5])
        amount = volume * close_price  # 估算成交额

        date_str = dt_str[:10]
        time_str = dt_str[11:] if len(dt_str) > 10 else ""
        if len(time_str) == 5:
            time_str = time_str + ":00"

        rows.append({
            "date": date_str,
            "time": time_str,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
            "amount": amount,
        })

    if not rows:
        return None

    df = pd.DataFrame(rows)
    df = df.sort_values(['date', 'time']).reset_index(drop=True)

    # 客户端筛选到请求日期范围
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    df = df[(df['date'] >= start_str) & (df['date'] <= end_str)].reset_index(drop=True)

    logger.info(
        f"[腾讯] 获取1分钟数据: {symbol} {start_date}~{end_date}, "
        f"共 {len(df)} 根K线, 覆盖 {df['date'].nunique()} 个交易日"
    )
    return df


def _symbol_to_em_secid(symbol: str) -> str:
    """
    将内部股票代码格式转换为东方财富secid格式。

    sh600749 → 1.600749（上海市场）
    sz000001 → 0.000001（深圳市场）
    """
    if symbol.startswith("sh"):
        return f"1.{symbol[2:]}"
    elif symbol.startswith("sz"):
        return f"0.{symbol[2:]}"
    return symbol


def fetch_minute_data_eastmoney(
    symbol: str,
    start_date: datetime.date,
    end_date: datetime.date,
) -> Optional[pd.DataFrame]:
    """
    从东方财富API获取历史1分钟K线数据（fallback数据源）。

    东方财富push2his API支持较长的历史回溯。
    每根K线格式: "datetime,open,close,high,low,volume,amount,amplitude"
    注意: 东方财富返回顺序是 open, close, high, low（与新浪不同）。

    Args:
        symbol: 股票代码（如 sh600749）
        start_date: 起始日期
        end_date: 结束日期

    Returns:
        DataFrame列: date, time, open, high, low, close, volume, amount, prev_close
    """
    secid = _symbol_to_em_secid(symbol)
    # 拉取更宽的日期范围（请求日前30天到今天），客户端再筛选，避免API日期过滤不准
    fetch_start = start_date - datetime.timedelta(days=40)
    beg = fetch_start.strftime('%Y%m%d')
    end = end_date.strftime('%Y%m%d')

    params = {
        "secid": secid,
        "klt": "1",          # 1分钟K线
        "fqt": "1",           # 前复权
        "beg": beg,
        "end": end,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ut": "7eea3edcaed734bea9cbfc24409bb989",
    }

    proxies = _get_proxies()
    try:
        resp = requests.get(
            EASTMONEY_KLINE_URL,
            params=params,
            proxies=proxies,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"东方财富API请求失败: {symbol} {e}")
        return None

    if not data or not data.get("data") or not data["data"].get("klines"):
        logger.warning(f"东方财富API返回空数据: {symbol} {start_date}~{end_date}")
        return None

    klines = data["data"]["klines"]
    if not klines:
        return None

    rows = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 7:
            continue
        # parts: [datetime, open, close, high, low, volume, amount, amplitude]
        dt_str = parts[0]       # "2026-09-03 09:31"
        open_price = float(parts[1])
        close_price = float(parts[2])
        high_price = float(parts[3])
        low_price = float(parts[4])
        volume = float(parts[5])
        amount = float(parts[6])

        date_str = dt_str[:10]
        time_str = dt_str[11:] if len(dt_str) > 10 else ""
        # 统一时间格式为 HH:MM:SS
        if len(time_str) == 5:
            time_str = time_str + ":00"

        rows.append({
            "date": date_str,
            "time": time_str,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
            "amount": amount,
        })

    if not rows:
        return None

    df = pd.DataFrame(rows)
    df = df.sort_values(['date', 'time']).reset_index(drop=True)

    # 客户端筛选到请求日期范围
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    df = df[(df['date'] >= start_str) & (df['date'] <= end_str)].reset_index(drop=True)

    logger.info(
        f"东方财富获取1分钟数据: {symbol} {start_date}~{end_date}, "
        f"共 {len(df)} 根K线, 覆盖 {df['date'].nunique()} 个交易日"
    )
    return df


def _get_prev_close_eastmoney(symbol: str, date_str: str) -> Optional[float]:
    """
    从东方财富获取指定日期前一交易日的收盘价。

    通过获取指定日期前一个交易日的日K线收盘价。
    """
    try:
        current_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return None

    prev_date = get_previous_trading_day(current_date)
    secid = _symbol_to_em_secid(symbol)

    # 获取prev_date的日K线（klt=101）
    params = {
        "secid": secid,
        "klt": "101",        # 日K线
        "fqt": "1",
        "beg": prev_date.strftime('%Y%m%d'),
        "end": prev_date.strftime('%Y%m%d'),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ut": "7eea3edcaed734bea9cbfc24409bb989",
    }

    proxies = _get_proxies()
    try:
        resp = requests.get(
            EASTMONEY_KLINE_URL,
            params=params,
            proxies=proxies,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        if data and data.get("data") and data["data"].get("klines"):
            line = data["data"]["klines"][0]
            parts = line.split(",")
            if len(parts) >= 3:
                # parts: [date, open, close, ...]
                return float(parts[2])
    except Exception as e:
        logger.error(f"东方财富获取prev_close失败: {symbol} {date_str}: {e}")

    return None


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

    if len(trading_days) > 10:
        logger.error(f"请求交易日数量过多: {len(trading_days)} 天（最大10天）")
        return None

    # 判断是否需要使用东方财富fallback
    today = datetime.date.today()
    days_from_today = len(get_trading_days(end_date, today))

    use_sina = days_from_today <= 7

    if use_sina:
        # === 主数据源：新浪财经 ===
        datalen = _calculate_datalen_for_range(start_date, end_date)
        raw_df = fetch_minute_data_raw(symbol, datalen)

        if raw_df is not None and not raw_df.empty:
            raw_df['date'] = raw_df['day'].apply(_extract_date_from_day)
            raw_df['time'] = raw_df['day'].apply(_extract_time_from_day)

            start_str = start_date.strftime('%Y-%m-%d')
            end_str = end_date.strftime('%Y-%m-%d')
            mask = (raw_df['date'] >= start_str) & (raw_df['date'] <= end_str)
            df = raw_df[mask].copy()

            if not df.empty:
                df = df.sort_values(['date', 'time']).reset_index(drop=True)
                df = _add_prev_close(symbol, df, raw_df)
                output_cols = ['date', 'time', 'open', 'high', 'low', 'close', 'volume', 'amount', 'prev_close']
                df = df[output_cols]
                logger.info(
                    f"[新浪] 获取1分钟数据: {symbol} {start_str}~{end_str}, "
                    f"共 {len(df)} 根K线"
                )
                return df

        logger.info(f"新浪未获取到数据，切换东方财富fallback: {symbol} {start_date}~{end_date}")

    # === Fallback数据源：东方财富 → 腾讯 ===
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')

    df = fetch_minute_data_eastmoney(symbol, start_date, end_date)

    if df is None or df.empty:
        logger.info(f"东方财富失败，切换腾讯: {symbol} {start_date}~{end_date}")
        df = fetch_minute_data_tencent(symbol, start_date, end_date)

    if df is None or df.empty:
        logger.error(f"新浪、东方财富、腾讯均未获取到数据: {symbol} {start_str}~{end_str}")
        return None

    # 为fallback数据添加prev_close（优先东方财富日线，新浪日线兜底）
    df['prev_close'] = None
    for date_str in sorted(df['date'].unique()):
        prev_close = _get_prev_close_eastmoney(symbol, date_str)
        if prev_close is None:
            # 新浪日线兜底
            try:
                daily_df = fetch_daily_data(symbol, datalen=20)
                if daily_df is not None and not daily_df.empty:
                    current_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
                    prev_date = get_previous_trading_day(current_date)
                    prev_date_str = prev_date.strftime('%Y-%m-%d')
                    prev_row = daily_df[daily_df['date'] == prev_date_str]
                    if not prev_row.empty:
                        prev_close = float(prev_row.iloc[0]['close'])
            except Exception as e:
                logger.warning(f"获取prev_close失败: {symbol} {date_str}: {e}")

        if prev_close is not None:
            df.loc[df['date'] == date_str, 'prev_close'] = prev_close

    output_cols = ['date', 'time', 'open', 'high', 'low', 'close', 'volume', 'amount', 'prev_close']
    df = df[output_cols]

    logger.info(
        f"[Fallback] 获取1分钟数据完成: {symbol} {start_str}~{end_str}, "
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
