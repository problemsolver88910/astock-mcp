"""
股票名称解析模块

维护股票名称→代码映射表，支持通过代码或名称解析股票信息。
- 代码自动判断市场：6开头→sh，0/3开头→sz
- 名称模糊匹配
- 内置常见股票、指数、ETF映射
- 可选：通过新浪实时API动态查询名称
"""

import re
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ============================================================
# 内置股票名称→代码映射表
# 格式: {名称: (symbol, type)}
# type: stock(股票), index(指数), etf(ETF)
# ============================================================

STOCK_MAP: Dict[str, Tuple[str, str]] = {
    # ---- 旅游股 ----
    "陕西旅游": ("sh603402", "stock"),
    "西藏旅游": ("sh600749", "stock"),
    "长白山": ("sh603099", "stock"),
    "西域旅游": ("sz300859", "stock"),
    "桂林旅游": ("sz000978", "stock"),
    "丽江股份": ("sz002033", "stock"),
    "丽江旅游": ("sz002033", "stock"),
    "黄山旅游": ("sh600054", "stock"),
    "峨眉山A": ("sz000888", "stock"),
    "张家界": ("sz000430", "stock"),
    "中青旅": ("sh600138", "stock"),
    "中国中免": ("sh601888", "stock"),
    "宋城演艺": ("sz300144", "stock"),
    "众信旅游": ("sz002707", "stock"),
    "凯撒旅业": ("sz000796", "stock"),
    "曲江文旅": ("sh600706", "stock"),
    "云南旅游": ("sz002059", "stock"),
    "三特索道": ("sz002159", "stock"),
    "大连圣亚": ("sh600593", "stock"),
    "天目湖": ("sh603136", "stock"),
    "九华旅游": ("sh603199", "stock"),
    "金陵饭店": ("sh601007", "stock"),
    "首旅酒店": ("sh600258", "stock"),
    "锦江酒店": ("sh600754", "stock"),
    "华天酒店": ("sz000428", "stock"),
    "全聚德": ("sz002186", "stock"),
    "西安旅游": ("sz000610", "stock"),
    "西安饮食": ("sz000721", "stock"),
    "岭南控股": ("sz000524", "stock"),
    "国旅联合": ("sh600358", "stock"),
    "号百控股": ("sh600640", "stock"),
    "北部湾旅": ("sh603869", "stock"),
    "腾邦国际": ("sz300178", "stock"),
    "探路者": ("sz300005", "stock"),
    "云南旅游": ("sz002059", "stock"),

    # ---- 银行股 ----
    "平安银行": ("sz000001", "stock"),
    "宁波银行": ("sz002142", "stock"),
    "浦发银行": ("sh600000", "stock"),
    "招商银行": ("sh600036", "stock"),
    "工商银行": ("sh601398", "stock"),
    "建设银行": ("sh601939", "stock"),
    "农业银行": ("sh601288", "stock"),
    "中国银行": ("sh601988", "stock"),
    "交通银行": ("sh601328", "stock"),
    "兴业银行": ("sh601166", "stock"),
    "民生银行": ("sh600016", "stock"),
    "中信银行": ("sh601998", "stock"),
    "光大银行": ("sh601818", "stock"),
    "华夏银行": ("sh600015", "stock"),
    "北京银行": ("sh601169", "stock"),
    "南京银行": ("sh601009", "stock"),
    "江苏银行": ("sh600919", "stock"),
    "上海银行": ("sh601229", "stock"),
    "杭州银行": ("sh600926", "stock"),
    "成都银行": ("sh601838", "stock"),
    "长沙银行": ("sh601577", "stock"),
    "郑州银行": ("sz002936", "stock"),
    "青岛银行": ("sz002948", "stock"),
    "西安银行": ("sh600928", "stock"),
    "苏州银行": ("sz002966", "stock"),
    "渝农商行": ("sh601077", "stock"),
    "青农商行": ("sz002958", "stock"),
    "紫金银行": ("sh601860", "stock"),

    # ---- 白酒/消费 ----
    "贵州茅台": ("sh600519", "stock"),
    "五粮液": ("sz000858", "stock"),
    "泸州老窖": ("sz000568", "stock"),
    "山西汾酒": ("sh600809", "stock"),
    "洋河股份": ("sz002304", "stock"),
    "古井贡酒": ("sz000596", "stock"),
    "水井坊": ("sh600779", "stock"),
    "舍得酒业": ("sh600702", "stock"),
    "酒鬼酒": ("sz000799", "stock"),
    "今世缘": ("sh603369", "stock"),
    "口子窖": ("sh603589", "stock"),
    "迎驾贡酒": ("sh603198", "stock"),
    "老白干酒": ("sh600559", "stock"),
    "伊力特": ("sh600197", "stock"),
    "金徽酒": ("sh603919", "stock"),
    "青青稞酒": ("sz002646", "stock"),
    "顺鑫农业": ("sz000860", "stock"),
    "维维股份": ("sh600300", "stock"),

    # ---- 新能源 ----
    "宁德时代": ("sz300750", "stock"),
    "比亚迪": ("sz002594", "stock"),
    "隆基绿能": ("sh601012", "stock"),
    "通威股份": ("sh600438", "stock"),
    "阳光电源": ("sz300274", "stock"),
    "亿纬锂能": ("sz300014", "stock"),
    "国轩高科": ("sz002074", "stock"),
    "赣锋锂业": ("sz002460", "stock"),
    "天齐锂业": ("sz002466", "stock"),
    "华友钴业": ("sh603799", "stock"),
    "恩捷股份": ("sz002812", "stock"),
    "璞泰来": ("sh603659", "stock"),
    "天赐材料": ("sz002709", "stock"),
    "新宙邦": ("sz300037", "stock"),
    "当升科技": ("sz300073", "stock"),
    "容百科技": ("sh688005", "stock"),
    "中伟股份": ("sz300919", "stock"),
    "先导智能": ("sz300450", "stock"),
    "汇川技术": ("sz300124", "stock"),

    # ---- 科技/半导体 ----
    "中芯国际": ("sh688981", "stock"),
    "韦尔股份": ("sh603501", "stock"),
    "兆易创新": ("sh603986", "stock"),
    "北方华创": ("sz002371", "stock"),
    "中微公司": ("sh688012", "stock"),
    "澜起科技": ("sh688008", "stock"),
    "紫光国微": ("sz002049", "stock"),
    "圣邦股份": ("sz300661", "stock"),
    "卓胜微": ("sz300782", "stock"),
    "立讯精密": ("sz002475", "stock"),
    "歌尔股份": ("sz002241", "stock"),
    "海康威视": ("sz002415", "stock"),
    "大华股份": ("sz002236", "stock"),
    "科大讯飞": ("sz002230", "stock"),
    "中兴通讯": ("sz000063", "stock"),
    "紫光股份": ("sz000938", "stock"),
    "浪潮信息": ("sz000977", "stock"),
    "中科曙光": ("sh603019", "stock"),
    "三六零": ("sh601360", "stock"),
    "用友网络": ("sh600588", "stock"),
    "金山办公": ("sh688111", "stock"),
    "广联达": ("sz002410", "stock"),
    "恒生电子": ("sh600570", "stock"),
    "同花顺": ("sz300033", "stock"),
    "东方财富": ("sz300059", "stock"),
    "指南针": ("sz300803", "stock"),
    "大智慧": ("sh601519", "stock"),
    "财富趋势": ("sh688318", "stock"),

    # ---- 医药 ----
    "恒瑞医药": ("sh600276", "stock"),
    "药明康德": ("sh603259", "stock"),
    "迈瑞医疗": ("sz300760", "stock"),
    "爱尔眼科": ("sz300015", "stock"),
    "片仔癀": ("sh600436", "stock"),
    "云南白药": ("sz000538", "stock"),
    "长春高新": ("sz000661", "stock"),
    "智飞生物": ("sz300122", "stock"),
    "泰格医药": ("sz300347", "stock"),
    "康龙化成": ("sz300759", "stock"),
    "凯莱英": ("sz002821", "stock"),
    "昭衍新药": ("sh603127", "stock"),
    "药石科技": ("sz300725", "stock"),
    "九洲药业": ("sh603456", "stock"),
    "普洛药业": ("sz000739", "stock"),
    "复星医药": ("sh600196", "stock"),
    "华东医药": ("sz000963", "stock"),
    "丽珠集团": ("sz000513", "stock"),
    "健康元": ("sh600380", "stock"),
    "科伦药业": ("sz002422", "stock"),
    "信立泰": ("sz002294", "stock"),
    "乐普医疗": ("sz300003", "stock"),
    "鱼跃医疗": ("sz002223", "stock"),
    "万东医疗": ("sh600055", "stock"),
    "通策医疗": ("sh600763", "stock"),
    "美年健康": ("sz002044", "stock"),
    "同仁堂": ("sh600085", "stock"),
    "白云山": ("sh600332", "stock"),
    "华润三九": ("sz000999", "stock"),
    "东阿阿胶": ("sz000423", "stock"),
    "以岭药业": ("sz002603", "stock"),
    "天士力": ("sh600535", "stock"),
    "康美药业": ("sh600518", "stock"),
    "白云山": ("sh600332", "stock"),

    # ---- 地产 ----
    "万科A": ("sz000002", "stock"),
    "保利发展": ("sh600048", "stock"),
    "招商蛇口": ("sz001979", "stock"),
    "金地集团": ("sh600383", "stock"),
    "新城控股": ("sh601155", "stock"),
    "华润置地": ("sh600340", "stock"),
    "华夏幸福": ("sh600340", "stock"),
    "金科股份": ("sz000656", "stock"),
    "阳光城": ("sz000671", "stock"),
    "中南建设": ("sz000961", "stock"),
    "荣盛发展": ("sz002146", "stock"),
    "蓝光发展": ("sh600466", "stock"),
    "华侨城A": ("sz000069", "stock"),
    "绿地控股": ("sh600606", "stock"),
    "中国建筑": ("sh601668", "stock"),
    "中国中铁": ("sh601390", "stock"),
    "中国铁建": ("sh601186", "stock"),
    "中国交建": ("sh601800", "stock"),
    "中国电建": ("sh601669", "stock"),
    "中国能建": ("sh601868", "stock"),
    "中国中冶": ("sh601618", "stock"),
    "中国化学": ("sh601117", "stock"),
    "中国核建": ("sh601611", "stock"),

    # ---- 周期/资源 ----
    "中国神华": ("sh601088", "stock"),
    "陕西煤业": ("sh601225", "stock"),
    "兖矿能源": ("sh600188", "stock"),
    "中煤能源": ("sh601898", "stock"),
    "山西焦煤": ("sz000983", "stock"),
    "平煤股份": ("sh601666", "stock"),
    "潞安环能": ("sh601699", "stock"),
    "阳泉煤业": ("sh600348", "stock"),
    "中国石油": ("sh601857", "stock"),
    "中国石化": ("sh600028", "stock"),
    "中国海油": ("sh600938", "stock"),
    "恒力石化": ("sh600346", "stock"),
    "荣盛石化": ("sz002493", "stock"),
    "东方盛虹": ("sz000301", "stock"),
    "宝钢股份": ("sh600019", "stock"),
    "包钢股份": ("sh600010", "stock"),
    "河钢股份": ("sz000709", "stock"),
    "鞍钢股份": ("sz000898", "stock"),
    "华菱钢铁": ("sz000932", "stock"),
    "南钢股份": ("sh600282", "stock"),
    "方大特钢": ("sh600507", "stock"),
    "中国铝业": ("sh601600", "stock"),
    "云铝股份": ("sz000807", "stock"),
    "神火股份": ("sz000933", "stock"),
    "紫金矿业": ("sh601899", "stock"),
    "山东黄金": ("sh600547", "stock"),
    "中金黄金": ("sh600489", "stock"),
    "赤峰黄金": ("sh600988", "stock"),
    "江西铜业": ("sh600362", "stock"),
    "云南铜业": ("sz000878", "stock"),
    "铜陵有色": ("sz000630", "stock"),
    "洛阳钼业": ("sh603993", "stock"),
    "华友钴业": ("sh603799", "stock"),
    "寒锐钴业": ("sz300618", "stock"),

    # ---- 军工 ----
    "中航沈飞": ("sh600760", "stock"),
    "中航西飞": ("sz000768", "stock"),
    "航发动力": ("sh600893", "stock"),
    "中航光电": ("sz002179", "stock"),
    "中航高科": ("sh600862", "stock"),
    "中航重机": ("sh600765", "stock"),
    "中航机电": ("sz002013", "stock"),
    "中航电子": ("sh600372", "stock"),
    "中直股份": ("sh600038", "stock"),
    "内蒙一机": ("sh600967", "stock"),
    "中国船舶": ("sh600150", "stock"),
    "中国重工": ("sh601989", "stock"),
    "中国海防": ("sh600764", "stock"),
    "中国动力": ("sh600482", "stock"),
    "中国卫星": ("sh600118", "stock"),
    "航天电子": ("sh600879", "stock"),
    "航天电器": ("sz002025", "stock"),
    "航天彩虹": ("sz002389", "stock"),
    "航天发展": ("sz000547", "stock"),
    "航天信息": ("sh600271", "stock"),
    "航天晨光": ("sh600501", "stock"),
    "航天长峰": ("sh600855", "stock"),
    "航天科技": ("sz000901", "stock"),
    "航天机电": ("sh600151", "stock"),
    "航天动力": ("sh600343", "stock"),
    "航天工程": ("sh603698", "stock"),
    "航天南湖": ("sh688552", "stock"),
    "国睿科技": ("sh600562", "stock"),
    "四创电子": ("sh600990", "stock"),
    "卫士通": ("sz002268", "stock"),
    "海格通信": ("sz002465", "stock"),
    "振华科技": ("sz000733", "stock"),
    "火炬电子": ("sh603678", "stock"),
    "鸿远电子": ("sh603267", "stock"),
    "宏达电子": ("sz300726", "stock"),
    "紫光国微": ("sz002049", "stock"),
    "高德红外": ("sz002414", "stock"),
    "大立科技": ("sz002214", "stock"),
    "睿创微纳": ("sh688002", "stock"),
    "久之洋": ("sz300516", "stock"),
    "新光光电": ("sh688011", "stock"),
    "铂力特": ("sh688333", "stock"),
    "西部超导": ("sh688122", "stock"),
    "西部材料": ("sz002149", "stock"),
    "宝钛股份": ("sh600456", "stock"),
    "钢研高纳": ("sz300034", "stock"),
    "图南股份": ("sz300855", "stock"),
    "抚顺特钢": ("sh600399", "stock"),

    # ---- 指数 ----
    "上证指数": ("sh000001", "index"),
    "上证综指": ("sh000001", "index"),
    "深证成指": ("sz399001", "index"),
    "深证成份指数": ("sz399001", "index"),
    "创业板指": ("sz399006", "index"),
    "创业板指数": ("sz399006", "index"),
    "沪深300": ("sh000300", "index"),
    "沪深300指数": ("sh000300", "index"),
    "中证500": ("sh000905", "index"),
    "中证500指数": ("sh000905", "index"),
    "中证1000": ("sh000852", "index"),
    "上证50": ("sh000016", "index"),
    "上证50指数": ("sh000016", "index"),
    "科创50": ("sh000688", "index"),
    "科创50指数": ("sh000688", "index"),
    "北证50": ("bj899050", "index"),
    "万得全A": ("sh881001", "index"),
    "中小板指": ("sz399005", "index"),
    "深证100": ("sz399330", "index"),
    "上证180": ("sh000010", "index"),
    "上证380": ("sh000009", "index"),
    "中证红利": ("sh000922", "index"),
    "中证白酒": ("sz399997", "index"),
    "中证医疗": ("sz399989", "index"),
    "中证新能源": ("sz399808", "index"),
    "中证半导体": ("sz931865", "index"),
    "中证军工": ("sz399967", "index"),
    "中证旅游": ("sz930919", "index"),
    "国证芯片": ("sz980017", "index"),
    "证券公司": ("sz399975", "index"),
    "中证银行": ("sz399986", "index"),
    "中证地产": ("sz930323", "index"),
    "中证消费": ("sz000932", "index"),
    "中证医药": ("sz000933", "index"),

    # ---- ETF ----
    "沪深300ETF": ("sh510300", "etf"),
    "300ETF": ("sh510300", "etf"),
    "中证500ETF": ("sh510500", "etf"),
    "500ETF": ("sh510500", "etf"),
    "创业板ETF": ("sz159915", "etf"),
    "创业板ETF易方达": ("sz159915", "etf"),
    "科创50ETF": ("sh588000", "etf"),
    "上证50ETF": ("sh510050", "etf"),
    "50ETF": ("sh510050", "etf"),
    "中证1000ETF": ("sh512100", "etf"),
    "1000ETF": ("sh512100", "etf"),
    "沪深300ETF易方达": ("sh510310", "etf"),
    "中证500ETF易方达": ("sh510580", "etf"),
    "创业板ETF广发": ("sz159952", "etf"),
    "证券ETF": ("sh512880", "etf"),
    "券商ETF": ("sh512000", "etf"),
    "银行ETF": ("sh512800", "etf"),
    "医药ETF": ("sh512010", "etf"),
    "医疗ETF": ("sh512170", "etf"),
    "消费ETF": ("sz159928", "etf"),
    "白酒ETF": ("sh512690", "etf"),
    "新能源ETF": ("sh516160", "etf"),
    "半导体ETF": ("sh512480", "etf"),
    "芯片ETF": ("sz159995", "etf"),
    "军工ETF": ("sh512660", "etf"),
    "国防ETF": ("sh512670", "etf"),
    "旅游ETF": ("sz159766", "etf"),
    "黄金ETF": ("sh518880", "etf"),
    "有色ETF": ("sh512400", "etf"),
    "煤炭ETF": ("sh515220", "etf"),
    "钢铁ETF": ("sh515210", "etf"),
    "房地产ETF": ("sh512200", "etf"),
    "基建ETF": ("sh516950", "etf"),
    "红利ETF": ("sh510880", "etf"),
    "价值ETF": ("sh510030", "etf"),
    "成长ETF": ("sh510050", "etf"),
    "MSCI中国A50ETF": ("sh560050", "etf"),
    "A50ETF": ("sh560050", "etf"),
    "恒生ETF": ("sz159920", "etf"),
    "恒生科技ETF": ("sh513180", "etf"),
    "中概互联ETF": ("sh513050", "etf"),
    "纳斯达克ETF": ("sh513100", "etf"),
    "标普500ETF": ("sh513500", "etf"),
    "日经225ETF": ("sh513520", "etf"),
    "德国ETF": ("sh513030", "etf"),
    "法国ETF": ("sh513080", "etf"),
    "华宝油气": ("sz162411", "etf"),
    "南方原油": ("sh501018", "etf"),
    "易方达原油": ("sh161129", "etf"),
    "豆粕ETF": ("sz159985", "etf"),
    "能源化工ETF": ("sz159981", "etf"),
    "有色金属ETF": ("sh512400", "etf"),
    "畜牧ETF": ("sz159865", "etf"),
    "养殖ETF": ("sz159865", "etf"),
    "农业ETF": ("sz159825", "etf"),
    "食品ETF": ("sh515710", "etf"),
    "酒ETF": ("sh512690", "etf"),
    "家电ETF": ("sz159996", "etf"),
    "汽车ETF": ("sh516110", "etf"),
    "智能汽车ETF": ("sh515250", "etf"),
    "新能源车ETF": ("sh515030", "etf"),
    "光伏ETF": ("sh515790", "etf"),
    "碳中和ETF": ("sh159790", "etf"),
    "环保ETF": ("sh512580", "etf"),
    "人工智能ETF": ("sh515980", "etf"),
    "AIETF": ("sh515980", "etf"),
    "计算机ETF": ("sh512720", "etf"),
    "软件ETF": ("sh515230", "etf"),
    "云计算ETF": ("sh516510", "etf"),
    "大数据ETF": ("sh515400", "etf"),
    "5GETF": ("sh515050", "etf"),
    "通信ETF": ("sh515880", "etf"),
    "传媒ETF": ("sh512980", "etf"),
    "游戏ETF": ("sz159869", "etf"),
    "教育ETF": ("sh513360", "etf"),
    "物流ETF": ("sh516910", "etf"),
    "航运ETF": ("sz159672", "etf"),
    "央企ETF": ("sh510060", "etf"),
    "国企ETF": ("sh517180", "etf"),
    "一带一路ETF": ("sh515150", "etf"),
    "长三角ETF": ("sh515600", "etf"),
    "粤港澳大湾区ETF": ("sz159963", "etf"),
    "京津冀ETF": ("sh515080", "etf"),
    "湖北ETF": ("sz159625", "etf"),
    "浙江ETF": ("sh515680", "etf"),
    "江苏ETF": ("sh515180", "etf"),
    "山东ETF": ("sh515660", "etf"),
    "福建ETF": ("sh515690", "etf"),
    "四川ETF": ("sz159628", "etf"),
    "重庆ETF": ("sh515650", "etf"),
    "河南ETF": ("sh515670", "etf"),
    "湖南ETF": ("sh515630", "etf"),
    "安徽ETF": ("sh515660", "etf"),
    "江西ETF": ("sh515660", "etf"),
    "陕西ETF": ("sh515660", "etf"),
    "山西ETF": ("sh515660", "etf"),
    "辽宁ETF": ("sh515660", "etf"),
    "吉林ETF": ("sh515660", "etf"),
    "黑龙江ETF": ("sh515660", "etf"),
    "云南ETF": ("sh515660", "etf"),
    "贵州ETF": ("sh515660", "etf"),
    "广西ETF": ("sh515660", "etf"),
    "海南ETF": ("sh515660", "etf"),
    "甘肃ETF": ("sh515660", "etf"),
    "青海ETF": ("sh515660", "etf"),
    "宁夏ETF": ("sh515660", "etf"),
    "新疆ETF": ("sh515660", "etf"),
    "西藏ETF": ("sh515660", "etf"),
    "内蒙古ETF": ("sh515660", "etf"),
    "香港ETF": ("sh513660", "etf"),
    "台湾ETF": ("sh513660", "etf"),
    "澳门ETF": ("sh513660", "etf"),
}

# 反向映射: symbol -> (name, type)
SYMBOL_MAP: Dict[str, Tuple[str, str]] = {}
for _name, (_symbol, _type) in STOCK_MAP.items():
    if _symbol not in SYMBOL_MAP:
        SYMBOL_MAP[_symbol] = (_name, _type)


def _infer_market(code: str) -> str:
    """
    根据股票代码推断市场前缀。

    规则：
    - 6开头 → sh（上海主板）
    - 9开头 → sh（上海B股）
    - 0开头 → sz（深圳主板/中小板）
    - 3开头 → sz（创业板）
    - 2开头 → sz（深圳B股）
    - 4/8开头 → bj（北交所）
    - 5开头 → sh（上海基金/ETF）
    - 1开头 → sz（深圳基金/ETF）
    - 7开头 → sh（上海新股申购）

    Args:
        code: 纯数字股票代码

    Returns:
        市场前缀 (sh/sz/bj)
    """
    if not code or not code[0].isdigit():
        return "sh"
    first = code[0]
    if first in ('6', '9', '5', '7'):
        return "sh"
    elif first in ('0', '3', '2', '1'):
        return "sz"
    elif first in ('4', '8'):
        return "bj"
    return "sh"


def resolve_stock(query: str) -> Optional[Dict[str, str]]:
    """
    解析股票查询，返回股票信息。

    支持以下输入格式：
    - 纯数字代码：如 "600749", "000001"
    - 带市场前缀代码：如 "sh600749", "sz000001"
    - 股票名称：如 "西藏旅游", "平安银行"
    - 指数名称：如 "上证指数", "沪深300"
    - ETF名称：如 "沪深300ETF"

    Args:
        query: 股票查询字符串

    Returns:
        包含 symbol, name, type 的字典，解析失败返回None
    """
    if not query or not query.strip():
        return None

    query = query.strip()
    query_lower = query.lower()

    # 1. 带市场前缀的代码 (如 sh600749, sz000001)
    m = re.match(r'^(sh|sz|bj)(\d{6})$', query_lower)
    if m:
        symbol = query_lower
        name, stock_type = SYMBOL_MAP.get(symbol, (query, "stock"))
        return {"symbol": symbol, "name": name, "type": stock_type}

    # 2. 纯数字代码
    if re.match(r'^\d{6}$', query):
        market = _infer_market(query)
        symbol = f"{market}{query}"
        name, stock_type = SYMBOL_MAP.get(symbol, (query, "stock"))
        return {"symbol": symbol, "name": name, "type": stock_type}

    # 3. 名称精确匹配
    if query in STOCK_MAP:
        symbol, stock_type = STOCK_MAP[query]
        return {"symbol": symbol, "name": query, "type": stock_type}

    # 4. 名称模糊匹配（包含关系）
    matches = []
    for name, (symbol, stock_type) in STOCK_MAP.items():
        if query in name or name in query:
            matches.append((name, symbol, stock_type))

    if matches:
        # 优先选择完全匹配或最短名称
        matches.sort(key=lambda x: len(x[0]))
        name, symbol, stock_type = matches[0]
        return {"symbol": symbol, "name": name, "type": stock_type}

    # 5. 尝试通过新浪API动态查询（仅对纯数字代码）
    # （在main.py中调用，此处不做网络请求）

    return None


def resolve_stocks(queries: List[str]) -> List[Dict[str, str]]:
    """
    批量解析股票查询。

    Args:
        queries: 股票查询字符串列表

    Returns:
        解析成功的股票信息列表（保持输入顺序，跳过解析失败的）
    """
    results = []
    for q in queries:
        resolved = resolve_stock(q)
        if resolved:
            results.append(resolved)
    return results


def fetch_stock_name_from_sina(symbol: str) -> Optional[str]:
    """
    通过新浪实时行情API查询股票名称。

    URL: https://hq.sinajs.cn/list=sh600749
    需要 Header: Referer: https://finance.sina.com.cn
    返回GBK编码，第一个字段为股票名称。

    Args:
        symbol: 带市场前缀的股票代码，如 sh600749

    Returns:
        股票名称，查询失败返回None
    """
    try:
        import requests
        url = f"https://hq.sinajs.cn/list={symbol}"
        headers = {
            "Referer": "https://finance.sina.com.cn",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        # 新浪返回GBK编码
        text = resp.content.decode('gbk', errors='replace')
        # 格式: var hq_str_sh600749="西藏旅游,10.50,10.45,...";
        match = re.search(r'="([^,]*)', text)
        if match:
            name = match.group(1).strip()
            if name:
                return name
    except Exception as e:
        logger.warning(f"从新浪查询股票名称失败 {symbol}: {e}")
    return None
