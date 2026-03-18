"""
risk_manager.py — 组合级风险控制

负责监控：
  1. 最大回撤保护（相对于启动时权益）
  2. 定期打印完整的持仓 / 盈亏概览
  3. 预警机制（接近风控线时提前告警）
"""

import logging
from typing import Optional

from config import BotConfig
from exchange_client import HyperliquidClient

logger = logging.getLogger(__name__)


class RiskManager:
    """组合风险管理器。"""

    def __init__(self, config: BotConfig, client: HyperliquidClient):
        self.config = config
        self.client = client
        self.initial_equity: Optional[float] = None
        self.is_stopped: bool = False

    # ──────────────────────────────────────────
    # 初始化
    # ──────────────────────────────────────────

    def initialize(self) -> None:
        """
        记录启动时的账户权益，作为回撤计算的基准。
        应在开始做市前调用一次。
        """
        equity = self.client.get_account_value()
        if equity > 0:
            self.initial_equity = equity
            logger.info(f"风险管理器初始化 | 基准权益: ${equity:,.2f}")
        else:
            logger.warning("无法获取账户权益，回撤保护功能已禁用")

    # ──────────────────────────────────────────
    # 风险检查
    # ──────────────────────────────────────────

    def check_drawdown(self) -> bool:
        """
        检查当前回撤是否超出限制。

        Returns:
            True  = 回撤触线，机器人应立即停止
            False = 正常，继续运行
        """
        if self.initial_equity is None or self.initial_equity <= 0:
            return False

        current_equity = self.client.get_account_value()
        if current_equity <= 0:
            logger.warning("无法获取当前权益，跳过回撤检查")
            return False

        drawdown_pct = (
            (self.initial_equity - current_equity) / self.initial_equity * 100
        )
        limit_pct = self.config.max_drawdown_pct

        if drawdown_pct >= limit_pct:
            logger.critical(
                f"⚠ 回撤触线！当前回撤 {drawdown_pct:.2f}% ≥ 限制 {limit_pct:.2f}%"
                f" | 初始权益: ${self.initial_equity:,.2f}"
                f" | 当前权益: ${current_equity:,.2f}"
            )
            self.is_stopped = True
            return True

        # 接近回撤线（80%）时预警
        if drawdown_pct >= limit_pct * 0.8:
            logger.warning(
                f"回撤预警: {drawdown_pct:.2f}% (限制 {limit_pct:.2f}%)"
            )

        return False

    # ──────────────────────────────────────────
    # 状态报告
    # ──────────────────────────────────────────

    def log_portfolio_status(self) -> None:
        """打印完整的账户持仓和盈亏概览。"""
        state = self.client.get_user_state()
        if not state:
            logger.warning("无法获取账户状态，跳过持仓报告")
            return

        summary = state.get("crossMarginSummary", {})
        equity = float(summary.get("accountValue", 0))
        margin_used = float(summary.get("totalMarginUsed", 0))
        total_ntl = float(summary.get("totalNtlPos", 0))

        logger.info("=" * 55)
        logger.info("持仓概览")
        logger.info(
            f"  权益: ${equity:>12,.2f} | 已用保证金: ${margin_used:>10,.2f}"
        )
        logger.info(f"  名义持仓总额: ${total_ntl:>10,.2f}")

        if self.initial_equity and self.initial_equity > 0:
            dd = (self.initial_equity - equity) / self.initial_equity * 100
            pnl = equity - self.initial_equity
            logger.info(
                f"  累计盈亏: ${pnl:>+10,.2f} | 回撤: {dd:.2f}%"
            )

        positions = state.get("assetPositions", [])
        active_positions = [
            p for p in positions if float(p["position"]["szi"]) != 0
        ]

        if active_positions:
            logger.info("  持仓明细:")
            total_unrealized = 0.0
            for pos_info in active_positions:
                pos = pos_info["position"]
                coin = pos["coin"]
                szi = float(pos["szi"])
                entry_px = float(pos.get("entryPx", 0) or 0)
                unrealized = float(pos.get("unrealizedPnl", 0) or 0)
                pos_val = float(pos.get("positionValue", 0) or 0)
                total_unrealized += unrealized

                side = "多" if szi > 0 else "空"
                logger.info(
                    f"    {coin:<4} {side} {abs(szi):.4f} 枚"
                    f" | 均价: ${entry_px:,.4f}"
                    f" | 名义: ${pos_val:,.1f}"
                    f" | 未实现盈亏: ${unrealized:+,.2f}"
                )
            logger.info(f"  未实现总盈亏: ${total_unrealized:+,.2f}")
        else:
            logger.info("  当前无持仓（delta 中性）")

        logger.info("=" * 55)
