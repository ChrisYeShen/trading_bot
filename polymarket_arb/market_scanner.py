"""
market_scanner.py — 扫描 Polymarket 活跃市场，分类并提取关键参数

分两类市场：
  1. WeatherMarket  — 关联城市 + 日期 + 指标（降水/温度/降雪）
  2. SportsMarket   — 关联赛事 + 球队 + 运动类别

解析规则：
  - 用 regex 匹配常见 Polymarket 市场问题句式
  - 匹配失败则跳过（不产生信号）
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from config import CITY_COORDS, SPORT_KEYWORDS, TEAM_ALIASES, WEATHER_KEYWORDS
from polymarket_client import MarketInfo

logger = logging.getLogger(__name__)

# ── 数据结构 ──────────────────────────────────────────────────

@dataclass
class WeatherMarket:
    market:      MarketInfo
    city:        str
    lat:         float
    lon:         float
    target_date: date
    metric:      str           # "precipitation" | "temperature_range" | "temperature_high"
                               # | "temperature_low" | "snow"
    threshold:   Optional[float] = None  # 单阈值（above/below 型）或区间下界（range 型）
    threshold_high: Optional[float] = None  # 区间上界（range 型专用）
    direction:   Optional[str]  = None   # "above" | "below" | "range"


@dataclass
class SportsMarket:
    market:     MarketInfo
    sport:      str          # "nfl" | "nba" | "epl" | ...
    home_team:  str
    away_team:  str
    winner_side: str         # "home" | "away" | "draw"（YES 代表哪队赢）


@dataclass
class FuturesMarket:
    market:        MarketInfo
    sport:         str    # "nba" | "nfl" | "ucl" | "world_cup" | ...
    team:          str    # 标准化球队/国家名（对应 OddsAPI）
    market_type:   str    # "winner" | "qualify"
    event:         str    # "2026 NBA Finals" | "2026 FIFA World Cup"


# ── 日期解析 ──────────────────────────────────────────────────

_MONTH_MAP = {
    "january":1,"february":2,"march":3,"april":4,
    "may":5,"june":6,"july":7,"august":8,
    "september":9,"october":10,"november":11,"december":12,
    "jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,
    "aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
}

def _parse_date(text: str) -> Optional[date]:
    """从文本中提取日期，支持多种格式"""
    text = text.lower()

    # "march 20", "march 20th", "mar 20"
    m = re.search(
        r"(january|february|march|april|may|june|july|august|"
        r"september|october|november|december|"
        r"jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?",
        text,
    )
    if m:
        month = _MONTH_MAP[m.group(1)]
        day   = int(m.group(2))
        year  = int(m.group(3)) if m.group(3) else datetime.now().year
        try:
            return date(year, month, day)
        except ValueError:
            pass

    # "2024-03-20" / "03/20/2024" / "03/20"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    m = re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else datetime.now().year
        try:
            return date(year, month, day)
        except ValueError:
            pass

    return None


def _find_city(text: str) -> Optional[tuple[str, float, float]]:
    """在文本中匹配已知城市，返回 (city_name, lat, lon)"""
    text_lower = text.lower()
    # 优先匹配更长的城市名（避免 "la" 误匹配 "la liga"）
    for city in sorted(CITY_COORDS.keys(), key=len, reverse=True):
        if city in text_lower:
            lat, lon = CITY_COORDS[city]
            return city, lat, lon
    return None


# ── 天气市场解析 ───────────────────────────────────────────────

def _parse_weather_market(market: MarketInfo) -> Optional[WeatherMarket]:
    text = (market.question + " " + market.description).lower()

    # 快速检查是否包含天气关键词
    if not any(kw in text for kw in WEATHER_KEYWORDS):
        return None

    city_info = _find_city(text)
    if not city_info:
        return None
    city, lat, lon = city_info

    target_date = _parse_date(text)
    if not target_date:
        return None

    # 判断市场类型
    if any(kw in text for kw in ["snow", "blizzard"]):
        return WeatherMarket(market, city, lat, lon, target_date, "snow")

    if any(kw in text for kw in ["rain", "precipitation", "precip"]):
        return WeatherMarket(market, city, lat, lon, target_date, "precipitation")

    # ── 温度区间（bracket）格式 ─────────────────────────────
    # 例："Will the highest temperature in Seattle be between 52-53°F on March 19?"
    range_m = re.search(
        r"temperature.*?between\s+(-?\d+(?:\.\d+)?)[–\-](-?\d+(?:\.\d+)?)\s*°?f",
        text,
    )
    if range_m:
        lo = float(range_m.group(1))
        hi = float(range_m.group(2))
        return WeatherMarket(market, city, lat, lon, target_date,
                             "temperature_range", lo, hi, "range")

    # ── 开放上界："X°F or higher / or above" ─────────────────
    # 例："Will the highest temperature in Seattle be 62°F or higher on March 19?"
    above_m = re.search(
        r"temperature.*?(-?\d+(?:\.\d+)?)\s*°?f\s+or\s+(?:higher|above)",
        text,
    )
    if above_m:
        return WeatherMarket(market, city, lat, lon, target_date,
                             "temperature_high", float(above_m.group(1)), None, "above")

    # ── 开放下界："X°F or lower / or below" ──────────────────
    # 例："Will the highest temperature in Seattle be 43°F or below on March 19?"
    below_m = re.search(
        r"temperature.*?(-?\d+(?:\.\d+)?)\s*°?f\s+or\s+(?:lower|below)",
        text,
    )
    if below_m:
        return WeatherMarket(market, city, lat, lon, target_date,
                             "temperature_low", float(below_m.group(1)), None, "below")

    # ── 传统 exceed/above/below 格式 ─────────────────────────
    # 例："Will the high temperature in NYC exceed 75°F on March 20?"
    temp_m = re.search(
        r"(high|low|temperature)\s+.*?"
        r"(exceed|above|over|below|under|at least|at most)\s+(-?\d+(?:\.\d+)?)\s*°?f",
        text,
    )
    if temp_m:
        metric    = "temperature_high" if "high" in temp_m.group(1) else "temperature_low"
        direction = "above" if temp_m.group(2) in ("exceed","above","over","at least") else "below"
        threshold = float(temp_m.group(3))
        return WeatherMarket(market, city, lat, lon, target_date,
                             metric, threshold, None, direction)

    # 没有明确指标
    return None


# ── 体育市场解析 ───────────────────────────────────────────────

def _detect_sport(text: str) -> Optional[str]:
    text_lower = text.lower()
    for sport, kws in SPORT_KEYWORDS.items():
        if any(kw in text_lower for kw in kws):
            return sport
    return None


# 常见 Polymarket 体育市场问题句式：
#   "Will the Lakers beat the Celtics?"
#   "Who wins: Lakers vs Celtics?"
#   "Lakers vs Celtics — who wins?"
_VS_PATTERNS = [
    re.compile(
        r"will (?:the )?(.+?) (?:beat|defeat|win (?:vs?\.?|against)) (?:the )?(.+?)[\?$]",
        re.I,
    ),
    re.compile(
        r"(?:the )?(.+?) vs\.? (?:the )?(.+?)(?:\s+[-—]\s+who wins)?",
        re.I,
    ),
    re.compile(
        r"who wins[:\s]+(?:the )?(.+?) or (?:the )?(.+?)[\?$]",
        re.I,
    ),
]


def _parse_sports_market(market: MarketInfo) -> Optional[SportsMarket]:
    text = market.question + " " + market.description
    sport = _detect_sport(text)
    if not sport:
        return None

    for pattern in _VS_PATTERNS:
        m = pattern.search(text)
        if m:
            team_a = m.group(1).strip().rstrip("?").strip()
            team_b = m.group(2).strip().rstrip("?").strip()

            # 规范化：去除 "the "
            team_a = re.sub(r"^the\s+", "", team_a, flags=re.I).strip()
            team_b = re.sub(r"^the\s+", "", team_b, flags=re.I).strip()

            # Polymarket YES = 第一个队（通常是问题里先提到的队）赢
            return SportsMarket(
                market       = market,
                sport        = sport,
                home_team    = team_a,
                away_team    = team_b,
                winner_side  = "home",   # YES → team_a 赢
            )

    return None


# ── 冠军/晋级 Futures 市场解析 ──────────────────────────────

# "Will the Lakers win the 2026 NBA Finals?"
# "Will Italy qualify for the 2026 FIFA World Cup?"
_WINNER_PAT   = re.compile(r"will (?:the )?(.+?) win (?:the )?(.+?)[\?\.]*$", re.I)
_QUALIFY_PAT  = re.compile(r"will (?:the )?(.+?) qualify (?:for|to) (?:the )?(.+?)[\?\.]*$", re.I)

# 事件关键词 → sport 类别
_EVENT_SPORT: list[tuple[re.Pattern, str]] = [
    (re.compile(r"nba finals|nba championship", re.I),        "nba"),
    (re.compile(r"super bowl",                  re.I),        "nfl"),
    (re.compile(r"champions league|ucl",        re.I),        "ucl"),
    (re.compile(r"world cup|fifa",              re.I),        "world_cup"),
    (re.compile(r"premier league|epl",          re.I),        "epl"),
    (re.compile(r"la liga",                     re.I),        "la_liga"),
    (re.compile(r"bundesliga",                  re.I),        "bundesliga"),
]


def _resolve_team(raw: str) -> Optional[str]:
    """将市场中提到的球队/国家映射到标准名称"""
    key = raw.strip().lower()
    # 直接命中
    if key in TEAM_ALIASES:
        return TEAM_ALIASES[key]
    # 部分匹配（alias 是 raw 的子串，或反之）
    for alias, official in TEAM_ALIASES.items():
        if alias in key or key in alias:
            return official
    # 兜底：首字母大写
    return raw.strip().title()


def _detect_event_sport(event_text: str) -> Optional[str]:
    for pat, sport in _EVENT_SPORT:
        if pat.search(event_text):
            return sport
    return None


def _parse_futures_market(market: MarketInfo) -> Optional[FuturesMarket]:
    text = market.question.strip()

    for pattern, market_type in [(_WINNER_PAT, "winner"), (_QUALIFY_PAT, "qualify")]:
        m = pattern.match(text)
        if not m:
            continue

        raw_team  = m.group(1).strip()
        raw_event = m.group(2).strip()

        sport = _detect_event_sport(raw_event) or _detect_sport(text)
        if not sport:
            return None

        team = _resolve_team(raw_team)
        return FuturesMarket(
            market       = market,
            sport        = sport,
            team         = team,
            market_type  = market_type,
            event        = raw_event,
        )

    return None


# ── 主入口 ────────────────────────────────────────────────────

@dataclass
class ScanResult:
    weather_markets: list[WeatherMarket] = field(default_factory=list)
    sports_markets:  list[SportsMarket]  = field(default_factory=list)
    futures_markets: list[FuturesMarket] = field(default_factory=list)
    skipped:         int = 0


def classify_markets(markets: list[MarketInfo]) -> ScanResult:
    """
    将原始 MarketInfo 列表分类为 WeatherMarket / SportsMarket / FuturesMarket。
    解析优先级：天气 → 冠军 futures → 单场对决
    """
    result = ScanResult()

    for m in markets:
        wm = _parse_weather_market(m)
        if wm:
            result.weather_markets.append(wm)
            continue

        fm = _parse_futures_market(m)
        if fm:
            result.futures_markets.append(fm)
            continue

        sm = _parse_sports_market(m)
        if sm:
            result.sports_markets.append(sm)
            continue

        result.skipped += 1
        logger.debug(f"跳过无法分类的市场: {m.question[:60]}")

    logger.info(
        f"分类完成 — 天气: {len(result.weather_markets)}  "
        f"冠军futures: {len(result.futures_markets)}  "
        f"单场: {len(result.sports_markets)}  跳过: {result.skipped}"
    )
    return result
