"""
main.py — Polymarket 天气 + 体育预测套利 Bot 主循环

用法：
  python main.py            # 正常运行（依据 .env 中 DRY_RUN 决定是否真实下单）
  python main.py --dry-run  # 强制 dry run，只打印信号
  python main.py --once     # 只跑一次扫描后退出（调试用）
"""
import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timezone

from config import BotConfig, FUTURES_SPORT_KEYS, ODDS_API_SPORT_KEYS, WEATHER_KEYWORDS, SPORT_KEYWORDS
from market_scanner import classify_markets
from odds_client import OddsClient
from polymarket_client import PolymarketClient
from signal_engine import generate_futures_signals, generate_sports_signals, generate_weather_signals
from trader import Trader

# ── 日志配置 ──────────────────────────────────────────────────
_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.stream.reconfigure(encoding="utf-8", errors="replace")  # Windows cp1252 fix

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        _stream_handler,
        logging.FileHandler("polymarket_arb.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

# 全局 flag：收到 SIGINT/SIGTERM 时优雅退出
_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info(f"收到信号 {signum}，准备优雅退出…")
    _shutdown = True


# ── 单次扫描 ──────────────────────────────────────────────────

def run_once(
    poly:         PolymarketClient,
    odds:         OddsClient,
    trader:       Trader,
    config:       BotConfig,
    sports_only:  bool = False,
    weather_only: bool = False,
) -> dict:
    """
    完整执行一次扫描-信号-下单流程。
    返回本次统计摘要字典。
    """
    t0 = time.time()
    logger.info("=" * 60)
    logger.info(f"开始扫描  [{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}]")

    # ── 1. 扫描 Polymarket 活跃市场 ────────────────────────────
    if sports_only:
        all_keywords = [kw for kws in SPORT_KEYWORDS.values() for kw in kws]
    elif weather_only:
        all_keywords = list(WEATHER_KEYWORDS)
    else:
        all_keywords = list(WEATHER_KEYWORDS) + [
            kw for kws in SPORT_KEYWORDS.values() for kw in kws
        ]
    raw_markets = poly.get_markets(keywords=all_keywords, limit=300)

    # ── 2. 并发填充订单簿（10 线程，~10x 提速）───────────────
    poly.enrich_batch(raw_markets, workers=10)

    # ── 3. 分类市场 ────────────────────────────────────────────
    scan = classify_markets(raw_markets)

    # ── 4. 获取体育赔率（单场 + 冠军 futures）─────────────────
    if not weather_only and config.odds_api_key:
        sport_keys   = list(ODDS_API_SPORT_KEYS.values())
        odds_games   = odds.get_all_sports_odds(sport_keys)
        all_futures  = odds.get_all_futures(FUTURES_SPORT_KEYS)
    else:
        odds_games   = []
        all_futures  = {}

    # ── 5. 生成信号 ────────────────────────────────────────────
    weather_sigs = [] if sports_only else generate_weather_signals(
        scan.weather_markets,
        min_edge       = config.min_edge,
        max_order_size = config.max_order_size,
    )
    sports_sigs = [] if weather_only else generate_sports_signals(
        scan.sports_markets,
        odds_games     = odds_games,
        min_edge       = config.min_edge,
        max_order_size = config.max_order_size,
    )
    futures_sigs = [] if weather_only else generate_futures_signals(
        scan.futures_markets,
        all_futures_odds = all_futures,
        min_edge         = config.min_edge,
        max_order_size   = config.max_order_size,
    )

    all_signals = weather_sigs + sports_sigs + futures_sigs
    triggered   = [s for s in all_signals if s.action != "PASS"]

    # ── 6. 打印信号汇总 ────────────────────────────────────────
    logger.info(f"信号统计: 天气={len(weather_sigs)} 单场={len(sports_sigs)} "
                f"冠军={len(futures_sigs)} 触发={len(triggered)}")

    if triggered:
        logger.info("─── 触发信号 ───────────────────────────────────")
        for sig in triggered:
            logger.info(
                f"  [{sig.category}/{sig.sub_type}] {sig.action:<8} "
                f"edge={sig.edge:+.1%}  size={sig.order_size:.1f} USDC\n"
                f"    {sig.market.question[:70]}\n"
                f"    {sig.detail}"
            )

    # ── 7. 执行下单 ────────────────────────────────────────────
    new_orders = trader.execute(all_signals)

    elapsed = time.time() - t0
    logger.info(f"本轮完成: 新订单={len(new_orders)}  {trader.summary()}  "
                f"耗时={elapsed:.1f}s")

    return {
        "markets_scanned":    len(raw_markets),
        "weather_markets":    len(scan.weather_markets),
        "sports_markets":     len(scan.sports_markets),
        "futures_markets":    len(scan.futures_markets),
        "signals_triggered":  len(triggered),
        "orders_placed":      len(new_orders),
        "elapsed_s":          elapsed,
    }


# ── 主函数 ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Polymarket Weather + Sports Arb Bot")
    parser.add_argument("--dry-run",      action="store_true", help="强制 dry run 模式")
    parser.add_argument("--once",         action="store_true", help="只跑一次后退出")
    parser.add_argument("--sports-only",  action="store_true", help="只扫描体育市场")
    parser.add_argument("--weather-only", action="store_true", help="只扫描天气市场")
    args = parser.parse_args()

    # 加载配置
    config = BotConfig.from_env()
    if args.dry_run:
        config.dry_run = True

    logger.info("=" * 60)
    logger.info("  Polymarket Weather + Sports Arb Bot  启动")
    logger.info(f"  dry_run={config.dry_run}  min_edge={config.min_edge:.0%}  "
                f"max_order={config.max_order_size} USDC  "
                f"interval={config.scan_interval}s")
    logger.info("=" * 60)

    if config.dry_run:
        logger.info("[DRY RUN 模式] 不会真实下单")

    # 初始化客户端
    try:
        poly   = PolymarketClient(config.private_key, config.host)
        odds   = OddsClient(config.odds_api_key)
        trader = Trader(
            client          = poly,
            max_open_orders = config.max_open_orders,
            max_order_size  = config.max_order_size,
            dry_run         = config.dry_run,
        )
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        sys.exit(1)

    # 注册退出信号
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # 主循环
    try:
        while not _shutdown:
            try:
                run_once(poly, odds, trader, config,
                         sports_only=args.sports_only,
                         weather_only=args.weather_only)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"扫描异常（将在下一轮重试）: {e}", exc_info=True)

            if args.once or _shutdown:
                break

            logger.info(f"等待 {config.scan_interval}s 后进行下一轮扫描…")
            # 分段 sleep，以便及时响应退出信号
            for _ in range(config.scan_interval):
                if _shutdown:
                    break
                time.sleep(1)

    finally:
        logger.info("正在撤销所有挂单…")
        cancelled = trader.cancel_all()
        logger.info(f"已撤销 {cancelled} 个挂单")
        logger.info("Bot 已停止")


if __name__ == "__main__":
    main()
