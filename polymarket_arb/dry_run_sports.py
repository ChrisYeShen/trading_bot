"""
dry_run_sports.py — 体育市场 Bot 离线 Dry Run

使用合成市场数据（NBA / NFL / 足球冠军 Futures）跑通完整信号流水线：
  市场扫描 → 分类 → 信号生成 → 风控 → (模拟)下单

用法：
  python dry_run_sports.py

无需任何 API 密钥或网络连接。
"""
import logging
import sys
from datetime import datetime, timezone

# ── 日志配置 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("dry_run")

# ── 导入项目模块 ───────────────────────────────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from polymarket_client import MarketInfo, OrderResult, PolymarketClient
from market_scanner import classify_markets
from signal_engine import generate_sports_signals, generate_futures_signals
from odds_client import GameOdds
from trader import Trader

# ═══════════════════════════════════════════════════════════════
# 1. 合成体育单场市场（模拟 Polymarket 已上线市场）
# ═══════════════════════════════════════════════════════════════

def _market(condition_id, question, yes_bid, yes_ask, yes_token="YES_TOK", no_token="NO_TOK"):
    m = MarketInfo(
        condition_id    = condition_id,
        question        = question,
        description     = question,
        yes_token_id    = yes_token + "_" + condition_id,
        no_token_id     = no_token + "_" + condition_id,
        accepting_orders= True,
        yes_best_bid    = yes_bid,
        yes_best_ask    = yes_ask,
        no_best_bid     = round(1.0 - yes_ask, 4),
        no_best_ask     = round(1.0 - yes_bid, 4),
    )
    return m

RAW_MARKETS = [
    # ── NBA 单场 ────────────────────────────────────────────
    # market prob ~55%，bookmaker prob ~65% → edge +10% → BUY_YES 触发
    _market("NBA001", "Will the Lakers beat the Celtics?",             0.52, 0.58),
    # market prob ~70%，bookmaker prob ~58% → edge -12% → BUY_NO 触发
    _market("NBA002", "Will the Warriors beat the Suns?",             0.67, 0.73),
    # market prob ~48%，bookmaker prob ~51% → edge +3% → PASS（不足 min_edge 8%）
    _market("NBA003", "Will the Heat beat the Nuggets?",              0.45, 0.51),

    # ── NFL 单场 ────────────────────────────────────────────
    # market prob ~38%，bookmaker prob ~48% → edge +10% → BUY_YES 触发
    _market("NFL001", "Will the Bills beat the Chiefs?",              0.35, 0.41),
    # spread 过宽（0.20）→ 无流动性，跳过
    _market("NFL002", "Will the Eagles beat the Cowboys?",            0.40, 0.60),

    # ── 足球单场 ───────────────────────────────────────────
    # market prob ~62%，bookmaker prob ~72% → edge +10% → BUY_YES 触发
    _market("SOC001", "Will Manchester City beat Arsenal?",           0.59, 0.65),
    # market prob ~28%，bookmaker prob ~35% → edge +7% → PASS
    _market("SOC002", "Will Liverpool beat Real Madrid?",             0.25, 0.31),

    # ── NBA 冠军 Futures ────────────────────────────────────
    # "Will the Celtics win the 2026 NBA Finals?" market~18%, odds~28% → edge+10%
    _market("FUT001", "Will the Celtics win the 2026 NBA Finals?",    0.15, 0.21),
    # "Will the Lakers win the 2026 NBA Finals?" market~22%, odds~15% → edge-7% → PASS
    _market("FUT002", "Will the Lakers win the 2026 NBA Finals?",     0.19, 0.25),

    # ── NFL Super Bowl Futures ──────────────────────────────
    # market~14%, odds~24% → edge+10%
    _market("FUT003", "Will the Chiefs win the Super Bowl?",          0.11, 0.17),

    # ── UCL Futures ─────────────────────────────────────────
    # market~20%, odds~30% → edge+10%
    _market("FUT004", "Will Manchester City win the Champions League?",0.17, 0.23),
]

# ═══════════════════════════════════════════════════════════════
# 2. 合成 OddsAPI 赔率数据
# ═══════════════════════════════════════════════════════════════

# 单场赔率（去 vig 后概率）
_NOW = datetime.now(timezone.utc).isoformat()

MOCK_ODDS_GAMES = [
    GameOdds(game_id="g1", sport_key="basketball_nba",
             home_team="Los Angeles Lakers", away_team="Boston Celtics",
             commence=_NOW, home_prob=0.65, away_prob=0.35),
    GameOdds(game_id="g2", sport_key="basketball_nba",
             home_team="Golden State Warriors", away_team="Phoenix Suns",
             commence=_NOW, home_prob=0.58, away_prob=0.42),
    GameOdds(game_id="g3", sport_key="basketball_nba",
             home_team="Miami Heat", away_team="Denver Nuggets",
             commence=_NOW, home_prob=0.51, away_prob=0.49),
    GameOdds(game_id="g4", sport_key="americanfootball_nfl",
             home_team="Buffalo Bills", away_team="Kansas City Chiefs",
             commence=_NOW, home_prob=0.48, away_prob=0.52),
    GameOdds(game_id="g5", sport_key="americanfootball_nfl",
             home_team="Philadelphia Eagles", away_team="Dallas Cowboys",
             commence=_NOW, home_prob=0.54, away_prob=0.46),
    GameOdds(game_id="g6", sport_key="soccer_epl",
             home_team="Manchester City", away_team="Arsenal",
             commence=_NOW, home_prob=0.72, away_prob=0.28),
    GameOdds(game_id="g7", sport_key="soccer_uefa_champs_league",
             home_team="Liverpool", away_team="Real Madrid",
             commence=_NOW, home_prob=0.35, away_prob=0.65),
]

# 冠军赔率（ball team → 去vig概率）
MOCK_FUTURES_ODDS = {
    "nba": {
        "Boston Celtics":         0.28,
        "Los Angeles Lakers":     0.15,
        "Golden State Warriors":  0.12,
        "Miami Heat":             0.09,
        "Denver Nuggets":         0.11,
    },
    "nfl": {
        "Kansas City Chiefs":     0.24,
        "Buffalo Bills":          0.18,
        "Philadelphia Eagles":    0.14,
        "Dallas Cowboys":         0.10,
    },
    "ucl": {
        "Manchester City":        0.30,
        "Real Madrid":            0.22,
        "Liverpool":              0.16,
        "Arsenal":                0.10,
        "Bayern Munich":          0.09,
    },
}

# ═══════════════════════════════════════════════════════════════
# 3. Mock PolymarketClient（只支持 dry-run 操作）
# ═══════════════════════════════════════════════════════════════

class MockPolymarketClient:
    """无需网络/私钥，仅用于 dry-run 信号测试。"""

    def get_markets(self, **_):
        return RAW_MARKETS

    def enrich_batch(self, markets, workers=1):
        return markets  # 合成数据已包含订单簿

    def place_limit_order(self, token_id, side, price, size, dry_run=True):
        label = "YES" if side == "BUY" else "NO"
        logger.info(f"[DRY] {side} {label}  token={token_id[:14]}…  price={price}  size={size} USDC")
        return OrderResult(success=True, order_id="DRY_RUN", side=label, price=price, size=size)

    def get_open_orders(self):
        return []

    def cancel_all_orders(self):
        return 0


# ═══════════════════════════════════════════════════════════════
# 4. 主流程
# ═══════════════════════════════════════════════════════════════

def main():
    MIN_EDGE       = 0.08   # 8%
    MAX_ORDER_SIZE = 50.0   # USDC

    logger.info("=" * 64)
    logger.info("  体育市场 Bot — Dry Run（离线模式）")
    logger.info(f"  min_edge={MIN_EDGE:.0%}  max_order={MAX_ORDER_SIZE} USDC  dry_run=True")
    logger.info(f"  合成市场数={len(RAW_MARKETS)}")
    logger.info("=" * 64)

    # ── 分类市场 ──────────────────────────────────────────────
    scan = classify_markets(RAW_MARKETS)

    logger.info(
        f"分类结果 — 单场: {len(scan.sports_markets)}  "
        f"冠军Futures: {len(scan.futures_markets)}  "
        f"天气: {len(scan.weather_markets)}  跳过: {scan.skipped}"
    )

    # ── 生成信号 ──────────────────────────────────────────────
    sports_sigs = generate_sports_signals(
        scan.sports_markets,
        odds_games     = MOCK_ODDS_GAMES,
        min_edge       = MIN_EDGE,
        max_order_size = MAX_ORDER_SIZE,
    )
    futures_sigs = generate_futures_signals(
        scan.futures_markets,
        all_futures_odds = MOCK_FUTURES_ODDS,
        min_edge         = MIN_EDGE,
        max_order_size   = MAX_ORDER_SIZE,
    )

    all_signals = sports_sigs + futures_sigs
    triggered   = [s for s in all_signals if s.action != "PASS"]

    logger.info("-" * 64)
    logger.info(f"信号统计: 单场={len(sports_sigs)}  冠军={len(futures_sigs)}  触发={len(triggered)}")

    # ── 详细输出触发信号 ──────────────────────────────────────
    if triggered:
        logger.info("─── 触发信号列表 ───────────────────────────────────")
        for sig in triggered:
            logger.info(
                f"  [{sig.category}/{sig.sub_type}] {sig.action:<8} "
                f"edge={sig.edge:+.1%}  size={sig.order_size:.1f} USDC\n"
                f"    Q: {sig.market.question}\n"
                f"    {sig.detail}"
            )
    else:
        logger.info("本次无触发信号（所有 edge 均低于阈值）")

    # ── 模拟下单 ──────────────────────────────────────────────
    mock_client = MockPolymarketClient()
    trader = Trader(
        client          = mock_client,
        max_open_orders = 10,
        max_order_size  = MAX_ORDER_SIZE,
        dry_run         = True,
    )

    logger.info("─── 模拟下单 ──────────────────────────────────────────")
    new_orders = trader.execute(all_signals)

    logger.info("=" * 64)
    logger.info(f"Dry Run 完成")
    logger.info(f"  已扫描市场:    {len(RAW_MARKETS)}")
    logger.info(f"  分类成功:      {len(scan.sports_markets) + len(scan.futures_markets)}")
    logger.info(f"  生成信号:      {len(all_signals)}")
    logger.info(f"  触发信号:      {len(triggered)}")
    logger.info(f"  模拟下单:      {len(new_orders)}")
    logger.info(f"  {trader.summary()}")
    logger.info("=" * 64)


if __name__ == "__main__":
    main()
