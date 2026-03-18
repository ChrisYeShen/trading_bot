"""
trader.py — 限价单执行 + 持仓/风控管理

职责：
  1. 接收 signal_engine 输出的 Signal 列表
  2. 检查风控条件（最大挂单数、最大单市场持仓）
  3. 执行限价做市单（GTC）
  4. 跟踪已下单的市场，防止重复下单
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from polymarket_client import OrderResult, PolymarketClient
from signal_engine import Signal

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    market_id:  str
    action:     str
    token_id:   str
    price:      float
    size:       float
    order_id:   str
    timestamp:  float = field(default_factory=time.time)
    filled:     bool  = False


class Trader:
    def __init__(
        self,
        client:          PolymarketClient,
        max_open_orders: int   = 10,
        max_order_size:  float = 50.0,
        dry_run:         bool  = True,
    ):
        self.client          = client
        self.max_open_orders = max_open_orders
        self.max_order_size  = max_order_size
        self.dry_run         = dry_run

        # 已下单记录：{condition_id: TradeRecord}
        self._active_orders: dict[str, TradeRecord] = {}
        # 本轮已成交市场（不再重复下单）：{condition_id}
        self._filled_markets: set[str] = set()

    # ── 风控检查 ──────────────────────────────────────────────

    def _can_trade(self, signal: Signal) -> tuple[bool, str]:
        cid = signal.market.condition_id

        # 已有活跃挂单
        if cid in self._active_orders:
            return False, "已有挂单"

        # 已成交，不重复
        if cid in self._filled_markets:
            return False, "已成交"

        # 超过最大挂单数
        open_count = len(self._active_orders)
        if open_count >= self.max_open_orders:
            return False, f"挂单数已达上限 ({open_count}/{self.max_open_orders})"

        # Edge 为 PASS
        if signal.action == "PASS":
            return False, "edge 不足，不下单"

        return True, ""

    # ── 执行 ─────────────────────────────────────────────────

    def execute(self, signals: list[Signal]) -> list[TradeRecord]:
        """
        执行所有触发的信号，返回本次成功下单记录。
        """
        # 同步挂单状态（检查已成交/已撤单）
        self._sync_order_status()

        records: list[TradeRecord] = []

        for sig in signals:
            if sig.action == "PASS":
                continue

            ok, reason = self._can_trade(sig)
            if not ok:
                logger.debug(f"跳过 [{sig.market.question[:40]}]: {reason}")
                continue

            size = min(sig.order_size, self.max_order_size)
            side = "BUY"  # YES token 或 NO token 都用 BUY

            result: OrderResult = self.client.place_limit_order(
                token_id  = sig.token_id,
                side      = side,
                price     = sig.limit_price,
                size      = size,
                dry_run   = self.dry_run,
            )

            if result.success:
                rec = TradeRecord(
                    market_id = sig.market.condition_id,
                    action    = sig.action,
                    token_id  = sig.token_id,
                    price     = sig.limit_price,
                    size      = size,
                    order_id  = result.order_id,
                )
                self._active_orders[sig.market.condition_id] = rec
                records.append(rec)
                logger.info(
                    f"{'[DRY]' if self.dry_run else '[LIVE]'} "
                    f"{sig.action} | {sig.market.question[:50]} | "
                    f"price={sig.limit_price:.3f} size={size:.1f} USDC | "
                    f"edge={sig.edge:+.1%} | {sig.detail}"
                )
            else:
                logger.error(
                    f"下单失败 [{sig.market.question[:40]}]: {result.error_msg}"
                )

        return records

    # ── 状态同步 ──────────────────────────────────────────────

    def _sync_order_status(self):
        """
        查询 Polymarket 当前挂单列表，将已成交/已撤单的记录从 _active_orders 移除。
        """
        if not self._active_orders:
            return

        try:
            open_orders = self.client.get_open_orders()
            open_ids = {o.get("id", o.get("orderID", "")) for o in open_orders}
        except Exception as e:
            logger.warning(f"获取挂单列表失败: {e}")
            return

        to_remove = []
        for cid, rec in self._active_orders.items():
            if rec.order_id not in open_ids and rec.order_id != "DRY_RUN":
                # 已成交或撤单
                logger.info(f"订单已完成: {rec.order_id} [{rec.action}]")
                self._filled_markets.add(cid)
                to_remove.append(cid)

        for cid in to_remove:
            del self._active_orders[cid]

    # ── 状态摘要 ──────────────────────────────────────────────

    def summary(self) -> str:
        return (
            f"活跃挂单: {len(self._active_orders)}  "
            f"已成交市场: {len(self._filled_markets)}"
        )

    def cancel_all(self) -> int:
        """撤销所有当前挂单（关闭时调用）"""
        return self.client.cancel_all_orders()
