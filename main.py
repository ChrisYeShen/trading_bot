"""
main.py — 主入口 & 主循环

运行流程：
  1. 从 .env 加载配置
  2. 初始化 Hyperliquid 客户端
  3. 清理历史遗留挂单
  4. 初始化风险管理器（记录基准权益）
  5. 启动每标的做市器
  6. 进入主循环（每 N 秒刷新报价 + 风险检查）
  7. 收到 Ctrl+C 或触发风控后优雅退出（撤销所有挂单）

日志同时写入 stdout 和 bot.log 文件。
"""

import logging
import signal
import sys
import time

from config import BotConfig
from exchange_client import HyperliquidClient
from market_maker import MarketMaker
from risk_manager import RiskManager

# ──────────────────────────────────────────
# 日志配置
# ──────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# 入口
# ──────────────────────────────────────────


def main() -> None:
    logger.info("=" * 60)
    logger.info(" Hyperliquid Delta-Neutral Market Maker 启动")
    logger.info("=" * 60)

    # 1. 加载配置
    try:
        config = BotConfig.from_env()
    except ValueError as exc:
        logger.critical(f"配置错误: {exc}")
        sys.exit(1)

    logger.info(
        f"网络: {'测试网' if config.use_testnet else '主网'} | "
        f"标的: {[s.symbol for s in config.symbols]} | "
        f"刷新间隔: {config.quote_refresh_interval}s"
    )

    # 2. 初始化交易所客户端
    try:
        client = HyperliquidClient(config.private_key, config.use_testnet)
    except Exception as exc:
        logger.critical(f"连接 Hyperliquid 失败: {exc}")
        sys.exit(1)

    # 3. 清理历史遗留挂单
    logger.info("检查并清理历史遗留挂单...")
    cancelled = client.cancel_all_orders()
    if cancelled:
        logger.info(f"已清理 {cancelled} 个遗留挂单")

    # 4. 初始化风险管理器
    risk_manager = RiskManager(config, client)
    risk_manager.initialize()

    # 5. 初始化每个标的的做市器
    market_makers = [MarketMaker(sym_cfg, client) for sym_cfg in config.symbols]

    # ──────────────────────────────────────────
    # 优雅退出
    # ──────────────────────────────────────────

    shutdown_flag = False

    def _handle_signal(signum, frame):
        nonlocal shutdown_flag
        logger.info(f"收到信号 {signum}，准备停机...")
        shutdown_flag = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ──────────────────────────────────────────
    # 主循环
    # ──────────────────────────────────────────

    logger.info("开始做市循环，按 Ctrl+C 停止")
    loop_count = 0

    while not shutdown_flag:
        loop_start = time.time()
        loop_count += 1

        try:
            # 风险检查
            if risk_manager.check_drawdown():
                logger.critical("已触发回撤保护，停机")
                break

            # 定期打印持仓报告
            if loop_count % config.portfolio_log_every == 1:
                risk_manager.log_portfolio_status()

            # 逐标的刷新报价
            for mm in market_makers:
                if shutdown_flag:
                    break
                try:
                    mm.update_quotes()
                except Exception as exc:
                    logger.error(
                        f"{mm.coin}: 更新报价时出现异常 — {exc}",
                        exc_info=True,
                    )

        except Exception as exc:
            logger.error(f"主循环异常: {exc}", exc_info=True)

        # 等待到下一轮
        elapsed = time.time() - loop_start
        sleep_time = max(0.0, config.quote_refresh_interval - elapsed)
        if sleep_time > 0 and not shutdown_flag:
            time.sleep(sleep_time)

    # ──────────────────────────────────────────
    # 停机清理
    # ──────────────────────────────────────────

    logger.info("正在撤销所有挂单，请稍候...")
    for mm in market_makers:
        try:
            mm.cancel_all()
        except Exception as exc:
            logger.error(f"{mm.coin}: 撤单时出现异常 — {exc}")

    # 打印最终持仓状态
    risk_manager.log_portfolio_status()

    logger.info("=" * 60)
    logger.info(" 机器人已停止")
    logger.info(
        f" 统计: "
        + " | ".join(
            f"{mm.coin} 报价{mm.quote_cycles}轮 成交{mm.detected_fills}次"
            for mm in market_makers
        )
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
