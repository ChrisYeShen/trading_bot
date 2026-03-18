"""
espn_client.py — ESPN 非官方 API 封装
无需 API Key，免费无限次调用（合理使用）

提供：
  - 获取近期/即将进行的比赛列表（NFL / NBA / 足球）
  - 获取球队信息（名称、缩写、别名）
  - 获取球员伤情报告
  - 获取赛事赔率（ESPN 内嵌赔率数据）
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 10
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; polymarket-bot/1.0)"}

# ESPN API 根地址
_SITE_API   = "https://site.api.espn.com/apis/site/v2/sports"
_CORE_API   = "https://sports.core.api.espn.com/v2/sports"

# 运动 → ESPN sport/league 路径
_SPORT_PATH: dict[str, str] = {
    "nfl":      "football/nfl",
    "nba":      "basketball/nba",
    "epl":      "soccer/eng.1",
    "ucl":      "soccer/uefa.champions",
    "la_liga":  "soccer/esp.1",
    "bundesliga": "soccer/ger.1",
    "serie_a":  "soccer/ita.1",
    "ligue_1":  "soccer/fra.1",
    "mls":      "soccer/usa.1",
}


@dataclass
class ESPNGame:
    game_id:    str
    sport:      str
    home_team:  str
    away_team:  str
    home_abbr:  str
    away_abbr:  str
    start_time: datetime
    status:     str                    # "pre" | "in" | "post"
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    # ESPN 内嵌赔率（美式赔率）
    home_moneyline: Optional[int] = None
    away_moneyline: Optional[int] = None


def _get(url: str, params: dict = None) -> dict:
    """带重试的 GET"""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=_HEADERS, params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt < 2:
                time.sleep(1.5 ** attempt)
                continue
            logger.error(f"ESPN request failed: {url} — {e}")
            raise
    raise RuntimeError(f"ESPN request failed: {url}")


def _parse_moneyline(odds_items: list) -> tuple[Optional[int], Optional[int]]:
    """从 ESPN odds 数组里提取主队/客队 moneyline"""
    home_ml = away_ml = None
    for item in odds_items:
        details = item.get("details", "")
        # 格式示例: "LAL -180" 或 "BOS +155"
        parts = details.strip().split()
        if len(parts) == 2:
            try:
                ml = int(parts[1])
                # ESPN 通常 homeTeamOdds / awayTeamOdds 字段
            except ValueError:
                pass
        home_odds = item.get("homeTeamOdds", {})
        away_odds = item.get("awayTeamOdds", {})
        if home_odds.get("moneyLine") is not None:
            home_ml = int(home_odds["moneyLine"])
        if away_odds.get("moneyLine") is not None:
            away_ml = int(away_odds["moneyLine"])
    return home_ml, away_ml


def get_games(sport: str, limit: int = 50) -> list[ESPNGame]:
    """
    获取指定运动近期/即将到来的比赛列表。
    sport: "nfl" | "nba" | "epl" | "ucl" | "la_liga" | ...
    """
    path = _SPORT_PATH.get(sport)
    if not path:
        raise ValueError(f"Unknown sport: {sport}. Valid: {list(_SPORT_PATH)}")

    url = f"{_SITE_API}/{path}/scoreboard"
    data = _get(url, params={"limit": limit})

    games: list[ESPNGame] = []
    for event in data.get("events", []):
        try:
            comp = event["competitions"][0]
            competitors = {c["homeAway"]: c for c in comp["competitors"]}
            home = competitors.get("home", {})
            away = competitors.get("away", {})

            # 比赛时间
            start_str = event.get("date", "")
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            except Exception:
                start_dt = datetime.now(timezone.utc)

            # 比赛状态
            status_type = event.get("status", {}).get("type", {})
            status = status_type.get("state", "pre")  # pre | in | post

            # 比分
            home_score = int(home["score"]) if home.get("score") not in (None, "") else None
            away_score = int(away["score"]) if away.get("score") not in (None, "") else None

            # 赔率
            odds_items = comp.get("odds", [])
            home_ml, away_ml = _parse_moneyline(odds_items)

            game = ESPNGame(
                game_id    = event["id"],
                sport      = sport,
                home_team  = home.get("team", {}).get("displayName", ""),
                away_team  = away.get("team", {}).get("displayName", ""),
                home_abbr  = home.get("team", {}).get("abbreviation", ""),
                away_abbr  = away.get("team", {}).get("abbreviation", ""),
                start_time = start_dt,
                status     = status,
                home_score = home_score,
                away_score = away_score,
                home_moneyline = home_ml,
                away_moneyline = away_ml,
            )
            games.append(game)
        except (KeyError, IndexError, TypeError) as e:
            logger.debug(f"Skipping malformed ESPN event {event.get('id', '?')}: {e}")
            continue

    logger.info(f"ESPN [{sport}]: {len(games)} games fetched")
    return games


def get_team_aliases(sport: str) -> dict[str, list[str]]:
    """
    返回 {team_display_name: [alias1, alias2, ...]} 映射表，
    用于将 Polymarket 市场问题中的球队名称匹配到 ESPN 数据。
    """
    path = _SPORT_PATH.get(sport)
    if not path:
        return {}

    url = f"{_SITE_API}/{path}/teams"
    try:
        data = _get(url, params={"limit": 100})
    except Exception as e:
        logger.warning(f"ESPN teams fetch failed for {sport}: {e}")
        return {}

    aliases: dict[str, list[str]] = {}
    for item in data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", []):
        team = item.get("team", {})
        name     = team.get("displayName", "")
        short    = team.get("shortDisplayName", "")
        nickname = team.get("name", "")
        abbr     = team.get("abbreviation", "")
        location = team.get("location", "")
        if name:
            aliases[name] = list({short, nickname, abbr, location, name} - {""})
    return aliases


def american_odds_to_prob(moneyline: int) -> float:
    """
    美式赔率 → 隐含概率（含 vig，未去 vig）
    +150 → 1/(1+1.5) = 0.4
    -200 → 2/(2+1)   = 0.667
    """
    if moneyline >= 0:
        return 100.0 / (moneyline + 100.0)
    else:
        return abs(moneyline) / (abs(moneyline) + 100.0)
