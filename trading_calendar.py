"""
A股交易日历模块

内置2026年A股休市日列表，提供交易日判断、交易日范围查询、
前一交易日获取等功能。

设计原则：
- 简单可靠：周一到周五且不在休市日列表中即为交易日
- 内置2026年完整休市日（含调休补班日处理）
- 可选：通过AKShare获取交易日历作为补充
"""

import datetime
from typing import List, Optional

# ============================================================
# 2026年A股休市日（含周末连休）
# 依据：国务院办公厅2026年节假日安排 + 沪深交易所休市安排
# ============================================================

# 元旦：1月1日（周四）休市，1月2日（周五）正常交易
# 春节：2月16日（周一）至2月22日（周日）休市，2月23日（周一）起照常开市
#   2月14日（周六）、2月15日（周日）为周末，2月16日-2月20日为春节假期
#   实际休市：2月16日(一)、17日(二)、18日(三)、19日(四)、20日(五)
# 清明：4月4日（周六）至4月6日（周一）休市，4月7日（周二）起照常开市
#   实际休市：4月6日(一)
# 劳动节：5月1日（周五）至5月5日（周二）休市，5月6日（周三）起照常开市
#   4月26日（周日）为补班日（正常交易）
#   实际休市：5月1日(五)、5月4日(一)、5月5日(二)
# 端午：6月19日（周五）至6月21日（周日）休市，6月22日（周一）起照常开市
#   实际休市：6月19日(五)
# 中秋+国庆：10月1日（周四）至10月8日（周四）休市，10月9日（周五）起照常开市
#   9月27日（周日）为补班日（正常交易）
#   10月10日（周六）为周末
#   实际休市：10月1日(四)、2日(五)、5日(一)、6日(二)、7日(三)、8日(四)

HOLIDAYS_2026 = {
    # 元旦
    datetime.date(2026, 1, 1),
    # 春节
    datetime.date(2026, 2, 16),
    datetime.date(2026, 2, 17),
    datetime.date(2026, 2, 18),
    datetime.date(2026, 2, 19),
    datetime.date(2026, 2, 20),
    # 清明
    datetime.date(2026, 4, 6),
    # 劳动节
    datetime.date(2026, 5, 1),
    datetime.date(2026, 5, 4),
    datetime.date(2026, 5, 5),
    # 端午
    datetime.date(2026, 6, 19),
    # 中秋+国庆
    datetime.date(2026, 10, 1),
    datetime.date(2026, 10, 2),
    datetime.date(2026, 10, 5),
    datetime.date(2026, 10, 6),
    datetime.date(2026, 10, 7),
    datetime.date(2026, 10, 8),
}

# 补班日（周末但正常交易）
# 2026年劳动节：4月26日（周日）补班
# 2026年国庆：9月27日（周日）补班
MAKEUP_WORKDAYS_2026 = {
    datetime.date(2026, 4, 26),
    datetime.date(2026, 9, 27),
}


def is_trading_day(date: datetime.date) -> bool:
    """
    判断给定日期是否为A股交易日。

    规则：
    - 周一至周五 且 不在休市日列表中 → 交易日
    - 补班日（周末调休）→ 交易日
    - 其他 → 非交易日

    Args:
        date: 待判断的日期

    Returns:
        True表示交易日，False表示非交易日
    """
    # 补班日优先判断（即使是周末也交易）
    if date in MAKEUP_WORKDAYS_2026:
        return True
    # 周末非交易日
    if date.weekday() >= 5:  # 5=周六, 6=周日
        return False
    # 法定假日非交易日
    if date in HOLIDAYS_2026:
        return False
    return True


def get_trading_days(start_date: datetime.date, end_date: datetime.date) -> List[datetime.date]:
    """
    获取指定日期范围内的所有交易日。

    Args:
        start_date: 起始日期（含）
        end_date: 结束日期（含）

    Returns:
        交易日列表，按日期升序排列
    """
    if start_date > end_date:
        return []
    trading_days = []
    current = start_date
    while current <= end_date:
        if is_trading_day(current):
            trading_days.append(current)
        current += datetime.timedelta(days=1)
    return trading_days


def get_previous_trading_day(date: datetime.date) -> datetime.date:
    """
    获取给定日期的前一个交易日。

    Args:
        date: 参考日期

    Returns:
        前一个交易日的日期
    """
    current = date - datetime.timedelta(days=1)
    while not is_trading_day(current):
        current -= datetime.timedelta(days=1)
        # 安全限制：最多回溯365天
        if (date - current).days > 365:
            break
    return current


def get_next_trading_day(date: datetime.date) -> datetime.date:
    """
    获取给定日期的后一个交易日。

    Args:
        date: 参考日期

    Returns:
        后一个交易日的日期
    """
    current = date + datetime.timedelta(days=1)
    while not is_trading_day(current):
        current += datetime.timedelta(days=1)
        if (current - date).days > 365:
            break
    return current


def count_trading_days(start_date: datetime.date, end_date: datetime.date) -> int:
    """
    计算指定日期范围内的交易日数量。

    Args:
        start_date: 起始日期（含）
        end_date: 结束日期（含）

    Returns:
        交易日数量
    """
    return len(get_trading_days(start_date, end_date))


def try_get_calendar_from_akshare() -> Optional[List[datetime.date]]:
    """
    尝试通过AKShare获取A股交易日历。
    如果不可用则返回None，调用方应回退到内置日历。

    Returns:
        交易日列表或None
    """
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        # 列名可能为 trade_date 或 其他
        date_col = None
        for col in df.columns:
            if 'date' in col.lower() or 'trade' in col.lower():
                date_col = col
                break
        if date_col is None:
            date_col = df.columns[0]
        dates = []
        for val in df[date_col]:
            if isinstance(val, str):
                dates.append(datetime.datetime.strptime(val, '%Y-%m-%d').date())
            elif isinstance(val, datetime.datetime):
                dates.append(val.date())
            elif isinstance(val, datetime.date):
                dates.append(val)
        return dates
    except Exception:
        return None
