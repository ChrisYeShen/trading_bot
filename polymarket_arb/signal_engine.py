"""
signal_engine.py — 计算套利信号 (edge)

天气信号：
  edge = NOAA_prob - market_implied_prob
  edge > +MIN_EDGE → 买 YES
  edge < -MIN_EDGE → 买 NO（卖 YES）

体育信号：
  edge = bookmaker_consensus_prob - market_implied_prob
  逻辑相同
"""
import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

import noaa_client
from market_scanner import FuturesMarket, SportsMarket, WeatherMarket
from odds_client import GameOdds, OddsClient
from polymarket_client import MarketInfo

logger = logging.getLogger(__name__)


# ── 信号数据结构 ───────────────────────────────────────────────

@dataclass
class Signal:
    market:         MarketInfo
    category:       str          # "weather" | "sports"
    sub_type:       str          # "precipitation" | "temperature" | "nfl" | "nba" | ...

    # 各方概率
    reference_prob: float        # NOAA 或去 vig 后博彩公司概率（YES 获胜概率）
    market_prob:    float        # Polymarket YES 隐含概率

    edge:           float        # reference_prob - market_prob
    action:         str          # "BUY_YES" | "BUY_NO" | "PASS"

    # 下单参数（由 trader.py 使用）
    token_id:       str
    limit_price:    float        # 挂单价格
    order_size:     float        # USDC

    # 附加信息（仅记录，不参与决策）
    detail:         str = ""


# ── 天气信号生成 ───────────────────────────────────────────────

def _weather_reference_prob(wm: WeatherMarket) -> Optional[float]:
    """从 NOAA 获取参考概率（0.0 ~ 1.0）"""
    try:
        if wm.metric == "precipitation":
            return noaa_client.get_precipitation_probability(wm.lat, wm.lon, wm.target_date)

        elif wm.metric == "snow":
            return noaa_client.get_snow_probability(wm.lat, wm.lon, wm.target_date)

        elif wm.metric in ("temperature_high", "temperature_low"):
            temps = noaa_client.get_temperature_forecast(wm.lat, wm.lon, wm.target_date)
            key = "high" if wm.metric == "temperature_high" else "low"
            forecast_temp = temps.get(key)
            if forecast_temp is None or wm.threshold is None:
                return None

            # 将点估计转换为概率（±5°F 的不确定性带，用线性插值）
            uncertainty = 5.0
            diff = forecast_temp - wm.threshold
            if wm.direction == "above":
                # P(temp > threshold)
                raw_prob = min(1.0, max(0.0, (diff + uncertainty) / (2 * uncertainty)))
            else:
                # P(temp < threshold)
                raw_prob = min(1.0, max(0.0, (-diff + uncertainty) / (2 * uncertainty)))
            return raw_prob

    except Exception as e:
        logger.warning(f"NOAA 查询失败 [{wm.city}/{wm.metric}]: {e}")
    return None


def generate_weather_signals(
    weather_markets: list[WeatherMarket],
    min_edge: float,
    max_order_size: float,
) -> list[Signal]:
    signals: list[Signal] = []

    for wm in weather_markets:
        market = wm.market
        if not _is_liquid(market):
            logger.debug(f"跳过无流动性市场: {market.question[:50]}")
            continue
        market_prob = market.implied_yes_prob
        if market_prob is None:
            logger.debug(f"跳过（无市场价格）: {market.question[:50]}")
            continue

        ref_prob = _weather_reference_prob(wm)
        if ref_prob is None:
            logger.debug(f"跳过（NOAA 无数据）: {market.question[:50]}")
            continue

        edge = ref_prob - market_prob
        action, token_id, limit_price = _decide_action(
            edge, market, min_edge
        )

        detail = (
            f"NOAA={ref_prob:.1%}  market={market_prob:.1%}  "
            f"edge={edge:+.1%}  city={wm.city}  date={wm.target_date}"
        )

        sig = Signal(
            market         = market,
            category       = "weather",
            sub_type       = wm.metric,
            reference_prob = ref_prob,
            market_prob    = market_prob,
            edge           = edge,
            action         = action,
            token_id       = token_id,
            limit_price    = limit_price,
            order_size     = _calc_order_size(edge, max_order_size),
            detail         = detail,
        )
        signals.append(sig)
        if action != "PASS":
            logger.info(f"[天气] {action}  {detail}")

    return signals


# ── 体育信号生成 ───────────────────────────────────────────────

def _match_game_to_market(
    sm: SportsMarket,
    games: list[GameOdds],
) -> Optional[GameOdds]:
    """
    将 Polymarket 体育市场与 OddsAPI 的赛事匹配（模糊字符串匹配）。
    """
    home_q = sm.home_team.lower()
    away_q = sm.away_team.lower()

    def _contains(a: str, b: str) -> bool:
        """a 包含 b 的任意一个词"""
        words = b.lower().split()
        return any(w in a.lower() for w in words if len(w) > 2)

    for game in games:
        ht = game.home_team.lower()
        at = game.away_team.lower()
        if (
            (_contains(ht, home_q) or _contains(home_q, ht)) and
            (_contains(at, away_q) or _contains(away_q, at))
        ) or (
            (_contains(ht, away_q) or _contains(away_q, ht)) and
            (_contains(at, home_q) or _contains(home_q, at))
        ):
            return game
    return None


def generate_sports_signals(
    sports_markets:  list[SportsMarket],
    odds_games:      list[GameOdds],
    min_edge:        float,
    max_order_size:  float,
) -> list[Signal]:
    signals: list[Signal] = []

    for sm in sports_markets:
        market = sm.market
        if not _is_liquid(market):
            logger.debug(f"跳过无流动性市场: {market.question[:50]}")
            continue
        market_prob = market.implied_yes_prob
        if market_prob is None:
            continue

        game = _match_game_to_market(sm, odds_games)
        if game is None:
            logger.debug(f"体育市场无赔率匹配: {market.question[:50]}")
            continue

        # Polymarket YES = sm.home_team 赢
        # 判断 sm.home_team 对应 OddsAPI 中的 home 还是 away
        is_home = sm.home_team.lower() in game.home_team.lower() or \
                  game.home_team.lower() in sm.home_team.lower()
        ref_prob = game.home_prob if is_home else game.away_prob
        if ref_prob is None:
            continue

        edge = ref_prob - market_prob
        action, token_id, limit_price = _decide_action(edge, market, min_edge)

        detail = (
            f"odds_prob={ref_prob:.1%}  market={market_prob:.1%}  "
            f"edge={edge:+.1%}  {sm.home_team} vs {sm.away_team}"
        )

        sig = Signal(
            market         = market,
            category       = "sports",
            sub_type       = sm.sport,
            reference_prob = ref_prob,
            market_prob    = market_prob,
            edge           = edge,
            action         = action,
            token_id       = token_id,
            limit_price    = limit_price,
            order_size     = _calc_order_size(edge, max_order_size),
            detail         = detail,
        )
        signals.append(sig)
        if action != "PASS":
            logger.info(f"[体育/{sm.sport}] {action}  {detail}")

    return signals


# ── 冠军 Futures 信号生成 ─────────────────────────────────────

def _fuzzy_team_lookup(team: str, futures_probs: dict[str, float]) -> Optional[float]:
    """在 OddsAPI 返回的球队名中模糊匹配，返回概率"""
    team_lower = team.lower()
    # 精确匹配
    if team in futures_probs:
        return futures_probs[team]
    # 部分匹配
    for official, prob in futures_probs.items():
        official_lower = official.lower()
        words = team_lower.split()
        if any(w in official_lower for w in words if len(w) > 3):
            return prob
        words2 = official_lower.split()
        if any(w in team_lower for w in words2 if len(w) > 3):
            return prob
    return None


_MAX_SPREAD = 0.15   # 超过 15% bid-ask spread 视为无流动性，跳过


def _is_liquid(market: MarketInfo) -> bool:
    """双边都有挂单且 spread 合理才认为有流动性"""
    bid = market.yes_best_bid
    ask = market.yes_best_ask
    if bid is None or ask is None:
        return False
    if ask <= bid:
        return False
    return (ask - bid) <= _MAX_SPREAD


def generate_futures_signals(
    futures_markets:  list[FuturesMarket],
    all_futures_odds: dict[str, dict[str, float]],   # {sport: {team: prob}}
    min_edge:         float,
    max_order_size:   float,
) -> list[Signal]:
    """
    冠军押注套利信号（只做 winner 类型，qualify 因缺少专项赔率数据暂跳过）。
    all_futures_odds 来自 OddsClient.get_all_futures()。
    """
    signals: list[Signal] = []

    for fm in futures_markets:
        # qualify 市场暂不处理（缺少专项赔率）
        if fm.market_type != "winner":
            continue

        market = fm.market

        # 流动性过滤：bid/ask 必须都存在且 spread ≤ 15%
        if not _is_liquid(market):
            logger.debug(f"跳过无流动性市场: {market.question[:60]}")
            continue

        market_prob = market.implied_yes_prob
        if market_prob is None:
            continue

        sport_odds = all_futures_odds.get(fm.sport, {})
        if not sport_odds:
            logger.debug(f"无冠军赔率数据 [{fm.sport}]: {market.question[:50]}")
            continue

        ref_prob = _fuzzy_team_lookup(fm.team, sport_odds)
        if ref_prob is None:
            logger.debug(f"未匹配到球队 [{fm.team}]: {market.question[:50]}")
            continue

        edge = ref_prob - market_prob
        action, token_id, limit_price = _decide_action(edge, market, min_edge)

        detail = (
            f"odds_prob={ref_prob:.1%}  market={market_prob:.1%}  "
            f"edge={edge:+.1%}  team={fm.team}  event={fm.event}"
        )

        sig = Signal(
            market         = market,
            category       = "sports",
            sub_type       = f"{fm.sport}_futures",
            reference_prob = ref_prob,
            market_prob    = market_prob,
            edge           = edge,
            action         = action,
            token_id       = token_id,
            limit_price    = limit_price,
            order_size     = _calc_order_size(edge, max_order_size),
            detail         = detail,
        )
        signals.append(sig)
        if action != "PASS":
            logger.info(f"[Futures/{fm.sport}] {action}  {detail}")

    return signals


# ── 共用工具 ──────────────────────────────────────────────────

def _decide_action(
    edge: float,
    market: MarketInfo,
    min_edge: float,
) -> tuple[str, str, float]:
    """
    返回 (action, token_id, limit_price)
    BUY_YES → 在 best_ask 挂买单（略低于 ask 以做 maker）
    BUY_NO  → 对应卖 YES，在 best_bid 挂卖单（略高于 bid）
    """
    if edge >= min_edge:
        # 买 YES：限价 = best_ask - 1 tick（0.01），仍能成为 maker
        price = round((market.yes_best_ask or 0.5) - 0.01, 4)
        price = max(0.01, min(0.99, price))
        return "BUY_YES", market.yes_token_id, price

    elif edge <= -min_edge:
        # 买 NO（= 卖 YES）：限价 = best_bid + 1 tick
        price = round((market.yes_best_bid or 0.5) + 0.01, 4)
        price = max(0.01, min(0.99, price))
        return "BUY_NO", market.no_token_id, price

    return "PASS", market.yes_token_id, 0.0


def _calc_order_size(edge: float, max_size: float) -> float:
    """
    Kelly 近似：下注比例 ∝ edge 大小，上限 max_size。
    简化版：size = max_size × min(|edge| / 0.20, 1.0)
    """
    fraction = min(abs(edge) / 0.20, 1.0)
    return round(max_size * fraction, 2)
