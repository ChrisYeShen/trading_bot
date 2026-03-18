"""
noaa_client.py — NOAA Weather API 封装
官方 API: https://api.weather.gov（免费，无需 key）

支持：
  - 获取指定城市的降水概率（precipitation probability）
  - 获取指定城市的高/低温预报
  - 自动缓存 grid point（每城市只查一次）
"""
import logging
import time
from datetime import date, datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# NOAA API base URL
_BASE = "https://api.weather.gov"
_HEADERS = {
    "User-Agent": "polymarket-weather-arb-bot/1.0 (contact@example.com)",
    "Accept": "application/json",
}
_TIMEOUT = 10  # 秒

# 城市 grid point 缓存：{(lat, lon): {"office": ..., "gridX": ..., "gridY": ..., "forecastUrl": ...}}
_grid_cache: dict[tuple[float, float], dict] = {}


def _get(url: str) -> dict:
    """带重试的 GET 请求"""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            if r.status_code == 503 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise
        except requests.RequestException as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"NOAA request failed after 3 attempts: {url}")


def _resolve_grid(lat: float, lon: float) -> dict:
    """查询并缓存城市对应的 NOAA grid point"""
    key = (round(lat, 4), round(lon, 4))
    if key in _grid_cache:
        return _grid_cache[key]

    url = f"{_BASE}/points/{lat},{lon}"
    data = _get(url)
    props = data["properties"]
    info = {
        "office":      props["gridId"],
        "gridX":       props["gridX"],
        "gridY":       props["gridY"],
        "forecastUrl": props["forecast"],         # 12-hour periods
        "hourlyUrl":   props["forecastHourly"],   # hourly
    }
    _grid_cache[key] = info
    logger.debug(f"Grid resolved for ({lat}, {lon}): {info['office']} {info['gridX']},{info['gridY']}")
    return info


def _parse_iso(s: str) -> datetime:
    """解析 NOAA 返回的 ISO-8601 时间字符串"""
    # 格式：2024-03-20T18:00:00-05:00
    return datetime.fromisoformat(s)


# ─── 公共接口 ─────────────────────────────────────────────────────────────────

def get_precipitation_probability(lat: float, lon: float, target_date: date) -> Optional[float]:
    """
    返回 target_date 当天的最高降水概率（0.0 ~ 1.0）。
    若 NOAA 数据中没有当天预报则返回 None。
    """
    grid = _resolve_grid(lat, lon)
    data = _get(grid["forecastUrl"])
    periods = data["properties"]["periods"]

    best_prob: Optional[float] = None
    for period in periods:
        period_start = _parse_iso(period["startTime"]).date()
        if period_start != target_date:
            continue
        pop = period.get("probabilityOfPrecipitation", {})
        value = pop.get("value")
        if value is not None:
            prob = float(value) / 100.0
            if best_prob is None or prob > best_prob:
                best_prob = prob

    return best_prob


def get_temperature_forecast(
    lat: float,
    lon: float,
    target_date: date,
) -> dict[str, Optional[float]]:
    """
    返回 target_date 的高温/低温预报（°F）。
    例：{"high": 78.0, "low": 55.0}
    """
    grid = _resolve_grid(lat, lon)
    data = _get(grid["forecastUrl"])
    periods = data["properties"]["periods"]

    result: dict[str, Optional[float]] = {"high": None, "low": None}
    for period in periods:
        period_start = _parse_iso(period["startTime"]).date()
        if period_start != target_date:
            continue
        temp = float(period["temperature"])
        unit = period.get("temperatureUnit", "F")
        if unit == "C":
            temp = temp * 9 / 5 + 32  # 转 °F
        if period.get("isDaytime", True):
            if result["high"] is None or temp > result["high"]:
                result["high"] = temp
        else:
            if result["low"] is None or temp < result["low"]:
                result["low"] = temp

    return result


def get_snow_probability(lat: float, lon: float, target_date: date) -> Optional[float]:
    """
    用降水概率 × P(温度 < 32°F) 估算降雪概率。
    简化做法：如果高温预报 ≤ 35°F，认为降水大概率为雪。
    返回 0.0 ~ 1.0。
    """
    pop = get_precipitation_probability(lat, lon, target_date)
    if pop is None:
        return None

    temps = get_temperature_forecast(lat, lon, target_date)
    high = temps.get("high")
    if high is None:
        return None

    # 简单线性插值：high ≤ 28°F → 100% 雪概率；high ≥ 40°F → 0%
    if high <= 28.0:
        snow_given_precip = 1.0
    elif high >= 40.0:
        snow_given_precip = 0.0
    else:
        snow_given_precip = (40.0 - high) / (40.0 - 28.0)

    return pop * snow_given_precip
