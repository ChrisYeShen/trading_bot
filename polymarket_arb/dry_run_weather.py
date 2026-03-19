"""
dry_run_weather.py — 天气市场 Bot 离线 Dry Run

使用合成市场数据（降水/温度/降雪 × 多城市）跑通完整信号流水线：
  市场扫描 → 分类 → NOAA 数据模拟 → 信号生成 → 风控 → (模拟)下单

用法：
  python dry_run_weather.py

无需任何 API 密钥或网络连接。
"""
import logging
import sys
import os
from datetime import date, datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

from polymarket_client import MarketInfo, OrderResult
from market_scanner import classify_markets
from signal_engine import generate_weather_signals
from trader import Trader

# ── 日志配置 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("dry_run_weather")


# ═══════════════════════════════════════════════════════════════
# 1. 合成天气市场（模拟 Polymarket 已上线市场）
# ═══════════════════════════════════════════════════════════════

def _market(cid, question, yes_bid, yes_ask):
    return MarketInfo(
        condition_id     = cid,
        question         = question,
        description      = question,
        yes_token_id     = f"YES_{cid}",
        no_token_id      = f"NO_{cid}",
        accepting_orders = True,
        yes_best_bid     = yes_bid,
        yes_best_ask     = yes_ask,
        no_best_bid      = round(1.0 - yes_ask, 4),
        no_best_ask      = round(1.0 - yes_bid, 4),
    )

# 今天 +3 天作为预报目标日期（NOAA 常见预报时效）
_TARGET = date(2026, 3, 22)
_DATE_STR = "March 22"

RAW_MARKETS = [
    # ── 降水市场 ────────────────────────────────────────────
    # NYC: market=30%, NOAA=50% → edge +20% → BUY_YES 触发
    _market("WEA001",
            f"Will it rain in New York on {_DATE_STR}?",
            0.27, 0.33),

    # Chicago: market=60%, NOAA=45% → edge -15% → BUY_NO 触发
    _market("WEA002",
            f"Will there be precipitation in Chicago on {_DATE_STR}?",
            0.57, 0.63),

    # Los Angeles: market=20%, NOAA=22% → edge +2% → PASS（不足 8%）
    _market("WEA003",
            f"Will it rain in Los Angeles on {_DATE_STR}?",
            0.17, 0.23),

    # ── 降雪市场 ────────────────────────────────────────────
    # Denver: market=35%, NOAA=55% → edge +20% → BUY_YES 触发
    _market("WEA004",
            f"Will it snow in Denver on {_DATE_STR}?",
            0.32, 0.38),

    # Miami: market=15%, NOAA=2% → edge -13% → BUY_NO 触发
    _market("WEA005",
            f"Will there be a blizzard in Miami on {_DATE_STR}?",
            0.12, 0.18),

    # ── 气温市场 ────────────────────────────────────────────
    # Seattle: "Will the high temperature exceed 55°F on March 22?"
    # market=45%, NOAA high=62°F → above 55 → prob≈0.85 → edge +40% → BUY_YES
    _market("WEA006",
            f"Will the high temperature in Seattle exceed 55°F on {_DATE_STR}?",
            0.42, 0.48),

    # Boston: "Will the low temperature exceed 38°F on March 22?"
    # market=70%, NOAA low=31°F → below 38 大概率 → prob≈0.18 → edge -52% → BUY_NO
    _market("WEA007",
            f"Will the high temperature in Boston exceed 38°F on {_DATE_STR}?",
            0.67, 0.73),

    # Dallas: "Will the high temperature exceed 72°F on March 22?"
    # market=55%, NOAA high=70°F → edge 很小 → PASS
    _market("WEA008",
            f"Will the high temperature in Dallas exceed 72°F on {_DATE_STR}?",
            0.52, 0.58),

    # ── 无流动性（spread > 15%）→ 跳过 ─────────────────────
    _market("WEA009",
            f"Will it rain in Houston on {_DATE_STR}?",
            0.30, 0.52),   # spread=0.22，过宽
]


# ═══════════════════════════════════════════════════════════════
# 2. 模拟 NOAA 返回数据
# ═══════════════════════════════════════════════════════════════

# 每个城市的模拟预报值
_NOAA_MOCK = {
    # city keyword → (precip_prob, high_f, low_f)
    "new york":    (0.50, 52.0, 41.0),
    "nyc":         (0.50, 52.0, 41.0),
    "chicago":     (0.45, 48.0, 34.0),
    "los angeles": (0.22, 72.0, 58.0),
    "la":          (0.22, 72.0, 58.0),
    "denver":      (0.70, 33.0, 20.0),   # cold → snow prob high
    "miami":       (0.10, 82.0, 70.0),   # warm → snow_given_precip=0
    "seattle":     (0.55, 62.0, 48.0),
    "boston":      (0.40, 36.0, 26.0),
    "dallas":      (0.30, 70.0, 52.0),
    "houston":     (0.35, 74.0, 60.0),
}


def _mock_precip(lat, lon, target_date):
    """根据 (lat, lon) 反查城市，返回模拟降水概率"""
    from config import CITY_COORDS
    for city, (clat, clon) in CITY_COORDS.items():
        if abs(clat - lat) < 0.01 and abs(clon - lon) < 0.01:
            return _NOAA_MOCK.get(city, (0.30, 65.0, 50.0))[0]
    return 0.30


def _mock_temp(lat, lon, target_date):
    from config import CITY_COORDS
    for city, (clat, clon) in CITY_COORDS.items():
        if abs(clat - lat) < 0.01 and abs(clon - lon) < 0.01:
            _, high, low = _NOAA_MOCK.get(city, (0.30, 65.0, 50.0))
            return {"high": high, "low": low}
    return {"high": 65.0, "low": 50.0}


def _mock_snow(lat, lon, target_date):
    """降水概率 × P(积雪)，与 noaa_client 逻辑一致"""
    pop = _mock_precip(lat, lon, target_date)
    temps = _mock_temp(lat, lon, target_date)
    high = temps.get("high", 65.0)
    if high <= 28.0:
        snow_frac = 1.0
    elif high >= 40.0:
        snow_frac = 0.0
    else:
        snow_frac = (40.0 - high) / (40.0 - 28.0)
    return pop * snow_frac


# ═══════════════════════════════════════════════════════════════
# 3. Mock PolymarketClient
# ═══════════════════════════════════════════════════════════════

class MockPolymarketClient:
    def get_open_orders(self):
        return []

    def place_limit_order(self, token_id, side, price, size, dry_run=True):
        label = "YES" if side == "BUY" else "NO"
        logger.info(f"[DRY] {side} {label}  token={token_id[:14]}…  price={price}  size={size} USDC")
        return OrderResult(success=True, order_id="DRY_RUN", side=label, price=price, size=size)

    def cancel_all_orders(self):
        return 0


# ═══════════════════════════════════════════════════════════════
# 4. 主流程
# ═══════════════════════════════════════════════════════════════

def main():
    MIN_EDGE       = 0.08
    MAX_ORDER_SIZE = 50.0

    logger.info("=" * 64)
    logger.info("  天气市场 Bot — Dry Run（离线模式）")
    logger.info(f"  min_edge={MIN_EDGE:.0%}  max_order={MAX_ORDER_SIZE} USDC  dry_run=True")
    logger.info(f"  目标日期: {_TARGET}  合成市场数: {len(RAW_MARKETS)}")
    logger.info("=" * 64)

    # 分类市场
    scan = classify_markets(RAW_MARKETS)
    logger.info(
        f"分类结果 — 天气: {len(scan.weather_markets)}  "
        f"单场: {len(scan.sports_markets)}  Futures: {len(scan.futures_markets)}  "
        f"跳过: {scan.skipped}"
    )

    # ── 打印分类详情 ──────────────────────────────────────────
    if scan.weather_markets:
        logger.info("─── 天气市场详情 ──────────────────────────────────")
        for wm in scan.weather_markets:
            logger.info(
                f"  [{wm.metric:20s}] city={wm.city:<15s} date={wm.target_date}  "
                f"bid={wm.market.yes_best_bid}  ask={wm.market.yes_best_ask}\n"
                f"    Q: {wm.market.question}"
            )

    # ── 用 mock 替换 noaa_client 的网络调用 ──────────────────
    with patch("noaa_client.get_precipitation_probability", side_effect=_mock_precip), \
         patch("noaa_client.get_temperature_forecast",      side_effect=_mock_temp), \
         patch("noaa_client.get_snow_probability",          side_effect=_mock_snow):

        weather_sigs = generate_weather_signals(
            scan.weather_markets,
            min_edge       = MIN_EDGE,
            max_order_size = MAX_ORDER_SIZE,
        )

    triggered = [s for s in weather_sigs if s.action != "PASS"]

    logger.info("-" * 64)
    logger.info(f"信号统计: 天气={len(weather_sigs)}  触发={len(triggered)}")

    if triggered:
        logger.info("─── 触发信号列表 ───────────────────────────────────")
        for sig in triggered:
            logger.info(
                f"  [{sig.sub_type:20s}] {sig.action:<8} "
                f"edge={sig.edge:+.1%}  size={sig.order_size:.1f} USDC\n"
                f"    Q: {sig.market.question}\n"
                f"    {sig.detail}"
            )
    else:
        logger.info("本次无触发信号")

    # ── 模拟下单 ──────────────────────────────────────────────
    trader = Trader(
        client          = MockPolymarketClient(),
        max_open_orders = 10,
        max_order_size  = MAX_ORDER_SIZE,
        dry_run         = True,
    )

    logger.info("─── 模拟下单 ──────────────────────────────────────────")
    new_orders = trader.execute(weather_sigs)

    # ── 未触发信号说明 ─────────────────────────────────────────
    passed = [s for s in weather_sigs if s.action == "PASS"]
    if passed:
        logger.info("─── PASS 信号（edge 不足/流动性过滤）──────────────")
        for sig in passed:
            logger.info(
                f"  PASS  edge={sig.edge:+.1%}  {sig.market.question[:60]}"
            )

    logger.info("=" * 64)
    logger.info("Dry Run 完成")
    logger.info(f"  已扫描市场:  {len(RAW_MARKETS)}")
    logger.info(f"  天气市场:    {len(scan.weather_markets)}")
    logger.info(f"  生成信号:    {len(weather_sigs)}")
    logger.info(f"  触发信号:    {len(triggered)}")
    logger.info(f"  模拟下单:    {len(new_orders)}")
    logger.info(f"  {trader.summary()}")
    logger.info("=" * 64)


if __name__ == "__main__":
    main()
