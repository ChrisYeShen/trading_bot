"""
odds_client.py — The Odds API 封装 + 去 vig 工具
https://the-odds-api.com/

功能：
  - 获取指定运动的近期赛事赔率（多家博彩公司）
  - 去 vig（去除庄家利润）后得到真实概率
  - 计算共识概率（多家博彩公司均值/中位数）

免费额度：500 次/月
"""
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE    = "https://api.the-odds-api.com/v4"
_TIMEOUT = 10

# The Odds API sport keys — 与 config.py 中 ODDS_API_SPORT_KEYS 对应
_REGIONS   = "us"         # 使用美国博彩公司
_MARKETS   = "h2h"        # 主客场胜负（head-to-head moneyline）
_ODDS_FMT  = "decimal"    # 小数赔率，便于计算


@dataclass
class BookmakerLine:
    bookmaker: str
    home_odds: float    # 小数赔率，如 1.80
    away_odds: float
    draw_odds: Optional[float] = None  # 足球有平局


@dataclass
class GameOdds:
    game_id:    str
    sport_key:  str
    home_team:  str
    away_team:  str
    commence:   str                         # ISO-8601 开赛时间
    lines:      list[BookmakerLine] = field(default_factory=list)

    # 去 vig 后的共识概率
    home_prob:  Optional[float] = None
    away_prob:  Optional[float] = None
    draw_prob:  Optional[float] = None


def _get(url: str, params: dict) -> tuple[dict | list, dict]:
    """带重试的 GET，返回 (data, response_headers)"""
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json(), dict(r.headers)
        except requests.RequestException as e:
            if attempt < 2:
                time.sleep(1.5 ** attempt)
                continue
            raise
    raise RuntimeError(f"Odds API request failed: {url}")


def _decimal_to_implied(odds: float) -> float:
    """小数赔率 → 隐含概率（含 vig）"""
    return 1.0 / odds if odds > 0 else 0.0


def devig_multiplicative(probs: list[float]) -> list[float]:
    """
    乘法去 vig（最常用方法）：
    将各隐含概率除以总过盘量（overround），使其归一化。
    """
    total = sum(probs)
    if total <= 0:
        return probs
    return [p / total for p in probs]


def devig_power(probs: list[float]) -> list[float]:
    """
    幂次去 vig（对两方市场更精确）：
    寻找指数 k 使得 sum((p_i)^(1/k)) = 1。
    """
    import math
    if len(probs) != 2:
        return devig_multiplicative(probs)
    # 二分法求 k
    lo, hi = 0.5, 5.0
    for _ in range(50):
        k = (lo + hi) / 2
        total = sum(p ** (1 / k) for p in probs)
        if total > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    raw = [p ** (1 / k) for p in probs]
    s = sum(raw)
    return [r / s for r in raw]


def consensus_prob(lines: list[BookmakerLine], use_power: bool = True) -> dict[str, Optional[float]]:
    """
    多家博彩公司去 vig 后取中位数概率。
    返回 {"home": float, "away": float, "draw": float | None}
    """
    import statistics

    home_probs, away_probs, draw_probs = [], [], []

    for line in lines:
        raw = [
            _decimal_to_implied(line.home_odds),
            _decimal_to_implied(line.away_odds),
        ]
        has_draw = line.draw_odds is not None and line.draw_odds > 1.0
        if has_draw:
            raw.append(_decimal_to_implied(line.draw_odds))

        cleaned = devig_multiplicative(raw)
        home_probs.append(cleaned[0])
        away_probs.append(cleaned[1])
        if has_draw and len(cleaned) > 2:
            draw_probs.append(cleaned[2])

    def _median(lst: list[float]) -> Optional[float]:
        return statistics.median(lst) if lst else None

    return {
        "home": _median(home_probs),
        "away": _median(away_probs),
        "draw": _median(draw_probs) if draw_probs else None,
    }


class OddsClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._remaining_requests: Optional[int] = None

    def get_odds(self, sport_key: str) -> list[GameOdds]:
        """
        获取指定运动所有即将进行赛事的赔率。
        sport_key 示例："americanfootball_nfl" / "basketball_nba" / "soccer_epl"
        """
        if not self.api_key:
            logger.warning("ODDS_API_KEY 未配置，跳过体育赔率获取")
            return []

        url = f"{_BASE}/sports/{sport_key}/odds"
        params = {
            "apiKey":    self.api_key,
            "regions":   _REGIONS,
            "markets":   _MARKETS,
            "oddsFormat": _ODDS_FMT,
        }

        try:
            data, headers = _get(url, params)
        except Exception as e:
            logger.error(f"OddsAPI fetch failed [{sport_key}]: {e}")
            return []

        # 记录剩余请求额度
        remaining = headers.get("x-requests-remaining")
        if remaining is not None:
            self._remaining_requests = int(remaining)
            if self._remaining_requests < 50:
                logger.warning(f"OddsAPI 剩余配额告急: {self._remaining_requests} 次")

        results: list[GameOdds] = []
        for event in data:
            go = GameOdds(
                game_id   = event["id"],
                sport_key = sport_key,
                home_team = event.get("home_team", ""),
                away_team = event.get("away_team", ""),
                commence  = event.get("commence_time", ""),
            )

            for bm in event.get("bookmakers", []):
                for market in bm.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                    line = BookmakerLine(
                        bookmaker = bm["key"],
                        home_odds = outcomes.get(go.home_team, 0.0),
                        away_odds = outcomes.get(go.away_team, 0.0),
                        draw_odds = outcomes.get("Draw"),
                    )
                    if line.home_odds > 1.0 and line.away_odds > 1.0:
                        go.lines.append(line)

            if go.lines:
                probs = consensus_prob(go.lines)
                go.home_prob = probs["home"]
                go.away_prob = probs["away"]
                go.draw_prob = probs["draw"]
                results.append(go)

        logger.info(f"OddsAPI [{sport_key}]: {len(results)} games with odds "
                    f"(remaining: {self._remaining_requests})")
        return results

    def get_all_sports_odds(self, sport_keys: list[str]) -> list[GameOdds]:
        """批量获取多个运动的赔率"""
        all_games: list[GameOdds] = []
        for key in sport_keys:
            games = self.get_odds(key)
            all_games.extend(games)
            time.sleep(0.3)  # 避免触发速率限制
        return all_games
