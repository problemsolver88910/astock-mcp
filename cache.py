"""
SQLite缓存层

使用SQLite数据库缓存1分钟行情数据，支持：
- 按股票+日期缓存完整交易日数据
- 缓存完整性标记（is_complete）
- 查询时优先返回完整缓存，不完整则重新获取
- 缓存状态查询

表结构：
- minute_quotes: 1分钟K线数据
- cache_metadata: 缓存完整性元数据
"""

import os
import sqlite3
import logging
import datetime
from typing import List, Optional, Dict, Any
from contextlib import contextmanager

import pandas as pd

logger = logging.getLogger(__name__)

# 数据库文件路径
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "cache.db")

# A股全天1分钟K线数量（9:30-11:30共120根，13:00-15:00共120根）
FULL_DAY_BARS = 240

# 缓存完整性判定阈值：新浪API偶尔缺少开盘集合竞价等少数K线，
# 实际返回235-240根均视为完整交易日
MIN_BARS_FOR_COMPLETE = 235


def init_db():
    """
    初始化数据库，创建data目录和表结构。
    启动时调用一次。
    """
    os.makedirs(DB_DIR, exist_ok=True)
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS minute_quotes (
                symbol      TEXT NOT NULL,
                date        TEXT NOT NULL,
                time        TEXT NOT NULL,
                open        REAL,
                high        REAL,
                low         REAL,
                close       REAL,
                volume      REAL,
                amount      REAL,
                prev_close  REAL,
                fetched_at  TEXT NOT NULL,
                PRIMARY KEY (symbol, date, time)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_minute_quotes_symbol_date
            ON minute_quotes(symbol, date)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache_metadata (
                symbol      TEXT NOT NULL,
                date        TEXT NOT NULL,
                is_complete INTEGER NOT NULL DEFAULT 0,
                bar_count   INTEGER NOT NULL DEFAULT 0,
                fetched_at  TEXT NOT NULL,
                PRIMARY KEY (symbol, date)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cache_metadata_symbol
            ON cache_metadata(symbol)
        """)
        conn.commit()
    logger.info(f"数据库初始化完成: {DB_PATH}")


@contextmanager
def get_conn():
    """
    获取数据库连接的上下文管理器。
    自动提交和关闭连接。
    """
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def save_to_cache(symbol: str, date: str, df: pd.DataFrame):
    """
    将1分钟行情数据保存到缓存。

    同时更新缓存元数据：
    - 检查当日K线数量是否完整（240根）
    - 只有完整的交易日数据才标记 is_complete=1

    Args:
        symbol: 股票代码（如 sh600749）
        date: 日期字符串（YYYY-MM-DD）
        df: 包含1分钟K线数据的DataFrame
            必须包含列: time, open, high, low, close, volume, amount, prev_close
    """
    if df is None or df.empty:
        logger.warning(f"缓存数据为空，跳过保存: {symbol} {date}")
        return

    fetched_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    bar_count = len(df)
    is_complete = 1 if bar_count >= MIN_BARS_FOR_COMPLETE else 0

    with get_conn() as conn:
        # 删除该股票该日期的旧数据
        conn.execute(
            "DELETE FROM minute_quotes WHERE symbol = ? AND date = ?",
            (symbol, date)
        )

        # 插入新数据
        rows = []
        for _, row in df.iterrows():
            rows.append((
                symbol,
                date,
                str(row.get('time', '')),
                float(row.get('open', 0)) if pd.notna(row.get('open')) else None,
                float(row.get('high', 0)) if pd.notna(row.get('high')) else None,
                float(row.get('low', 0)) if pd.notna(row.get('low')) else None,
                float(row.get('close', 0)) if pd.notna(row.get('close')) else None,
                float(row.get('volume', 0)) if pd.notna(row.get('volume')) else None,
                float(row.get('amount', 0)) if pd.notna(row.get('amount')) else None,
                float(row.get('prev_close', 0)) if pd.notna(row.get('prev_close')) else None,
                fetched_at
            ))
        conn.executemany(
            """INSERT INTO minute_quotes
               (symbol, date, time, open, high, low, close, volume, amount, prev_close, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows
        )

        # 更新缓存元数据（REPLACE）
        conn.execute(
            """INSERT OR REPLACE INTO cache_metadata
               (symbol, date, is_complete, bar_count, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            (symbol, date, is_complete, bar_count, fetched_at)
        )
        conn.commit()

    logger.info(
        f"缓存已保存: {symbol} {date}, bar_count={bar_count}, "
        f"is_complete={is_complete}"
    )


def load_from_cache(symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """
    从缓存加载指定日期范围的1分钟行情数据。

    只有当所有日期的缓存都完整（is_complete=1）时才返回数据，
    否则返回None，调用方应重新从数据源获取。

    Args:
        symbol: 股票代码（如 sh600749）
        start_date: 起始日期（YYYY-MM-DD）
        end_date: 结束日期（YYYY-MM-DD）

    Returns:
        包含1分钟K线数据的DataFrame，如果缓存不完整则返回None
    """
    with get_conn() as conn:
        # 检查日期范围内所有日期的缓存完整性
        cursor = conn.execute(
            """SELECT date, is_complete, bar_count
               FROM cache_metadata
               WHERE symbol = ? AND date >= ? AND date <= ?
               ORDER BY date""",
            (symbol, start_date, end_date)
        )
        metadata = cursor.fetchall()

        if not metadata:
            logger.debug(f"缓存未命中: {symbol} {start_date} ~ {end_date}")
            return None

        # 检查是否所有日期都完整
        for row in metadata:
            if row['is_complete'] != 1:
                logger.debug(
                    f"缓存不完整: {symbol} {row['date']}, "
                    f"bar_count={row['bar_count']}"
                )
                return None

        # 加载数据
        cursor = conn.execute(
            """SELECT time, open, high, low, close, volume, amount, prev_close
               FROM minute_quotes
               WHERE symbol = ? AND date >= ? AND date <= ?
               ORDER BY date, time""",
            (symbol, start_date, end_date)
        )
        rows = cursor.fetchall()

        if not rows:
            return None

        df = pd.DataFrame([dict(r) for r in rows])
        logger.info(
            f"缓存命中: {symbol} {start_date} ~ {end_date}, "
            f"共 {len(df)} 根K线"
        )
        return df


def is_cache_complete(symbol: str, date: str) -> bool:
    """
    检查指定股票指定日期的缓存是否完整。

    Args:
        symbol: 股票代码
        date: 日期（YYYY-MM-DD）

    Returns:
        True表示缓存完整，False表示不完整或不存在
    """
    with get_conn() as conn:
        cursor = conn.execute(
            "SELECT is_complete FROM cache_metadata WHERE symbol = ? AND date = ?",
            (symbol, date)
        )
        row = cursor.fetchone()
        if row and row['is_complete'] == 1:
            return True
    return False


def get_cache_status(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    查询缓存状态。

    Args:
        symbol: 可选，指定股票代码；为None时返回所有股票的缓存状态

    Returns:
        缓存状态列表，每项包含 symbol, date, is_complete, bar_count, fetched_at
    """
    with get_conn() as conn:
        if symbol:
            cursor = conn.execute(
                """SELECT symbol, date, is_complete, bar_count, fetched_at
                   FROM cache_metadata
                   WHERE symbol = ?
                   ORDER BY date DESC""",
                (symbol,)
            )
        else:
            cursor = conn.execute(
                """SELECT symbol, date, is_complete, bar_count, fetched_at
                   FROM cache_metadata
                   ORDER BY date DESC, symbol
                   LIMIT 500"""
            )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def get_cache_stats() -> Dict[str, Any]:
    """
    获取缓存统计信息。

    Returns:
        包含总缓存条目数、完整缓存数、不完整缓存数、覆盖股票数等
    """
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) as cnt FROM cache_metadata").fetchone()['cnt']
        complete = conn.execute(
            "SELECT COUNT(*) as cnt FROM cache_metadata WHERE is_complete = 1"
        ).fetchone()['cnt']
        incomplete = total - complete
        symbols = conn.execute(
            "SELECT COUNT(DISTINCT symbol) as cnt FROM cache_metadata"
        ).fetchone()['cnt']
        bars = conn.execute("SELECT COUNT(*) as cnt FROM minute_quotes").fetchone()['cnt']

        return {
            "total_cache_entries": total,
            "complete_entries": complete,
            "incomplete_entries": incomplete,
            "unique_symbols": symbols,
            "total_bars_cached": bars,
            "db_path": DB_PATH,
        }


def clear_cache(symbol: Optional[str] = None, date: Optional[str] = None):
    """
    清除缓存。

    Args:
        symbol: 可选，指定股票代码
        date: 可选，指定日期
    """
    with get_conn() as conn:
        if symbol and date:
            conn.execute(
                "DELETE FROM minute_quotes WHERE symbol = ? AND date = ?",
                (symbol, date)
            )
            conn.execute(
                "DELETE FROM cache_metadata WHERE symbol = ? AND date = ?",
                (symbol, date)
            )
        elif symbol:
            conn.execute("DELETE FROM minute_quotes WHERE symbol = ?", (symbol,))
            conn.execute("DELETE FROM cache_metadata WHERE symbol = ?", (symbol,))
        else:
            conn.execute("DELETE FROM minute_quotes")
            conn.execute("DELETE FROM cache_metadata")
        conn.commit()
    logger.info(f"缓存已清除: symbol={symbol}, date={date}")
