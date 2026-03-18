"""
market_maker.py — 单标的 Delta-Neutral 做市策略

核心思路（库存偏斜报价）
────────────────────────
设 mid 为当前中间价，spread 为目标价差，inventory_ratio ∈ [-1, 1] 为
当前持仓与最大持仓之比：

    reservation_mid = mid × (1 - inventory_ratio × spread/2)

    bid = reservation_mid × (1 - spread/2)
    ask = reservation_mid × (1 + spread/2)

当 inventory_ratio > 0（净多头）时：
  - reservation_mid 下移 → ask 价格降低 → 更容易被吃卖单
  - 从而自然减少多头敞口，趋近 delta 中性

当 inventory_ratio < 0（净空头）时：
  - reservation_mid 上移 → bid 价格升高 → 更容易被吃买单
  - 从而自然减少空头敞口

当持仓接近单侧上限时，停止该方向的报价。
"""

import logging
import math
from typing import Optional, Tuple

from config import SymbolConfig
from exchange_client import HyperliquidClient

logger = logging.getLogger(__name__)

# 每个标的的价格精度（有效位数）
_PRICE_SIG_FIGS = 5


def _round_price(price: float) -> float:
    """将价格四舍五入到 5 位有效数字（Hyperliquid 要求）。"""
    if price <= 0:
        return price
    magnitude = math.floor(math.log10(price))
    decimal_places = max(0, _PRICE_SIG_FIGS - 1 - magnitude)
    return round(price, decimal_places)


class MarketMaker:
    """单标的做市管理器。"""

    def __init__(self, config: SymbolConfig, client: HyperliquidClient):
        self.config = config
        self.client = client
        self.coin = config.symbol

        # 当前活跃订单的 ID（None 表示无挂单）
        self.bid_oid: Optional[int] = None
        self.ask_oid: Optional[int] = None

        # 统计
        self.quote_cycles: int = 0       # 已完成的报价轮次
        self.detected_fills: int = 0     # 检测到的成交次数

    # ──────────────────────────────────────────
    # 核心报价逻辑
    # ──────────────────────────────────────────

    def _calc_reservation_mid(self, mid: float, inventory_ratio: float) -> float:
        """
        计算带库存偏斜的"保留中间价"。

        inventory_ratio ∈ [-1, 1]：
          +1 = 满仓多头 → 压低保留中间价，鼓励卖出
          -1 = 满仓空头 → 抬高保留中间价，鼓励买入
        """
        half_spread = self.config.spread_bps / 2 / 10_000
        skew = inventory_ratio * half_spread  # 最大偏移 = 半价差
        return mid * (1.0 - skew)

    def calc_quotes(
        self, mid: float, position_coins: float
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        计算本轮的 bid / ask 报价。

        Returns:
            (bid_price, ask_price)，None 表示该方向不报价。
        """
        max_pos_usd = self.config.max_position_usd
        position_usd = position_coins * mid

        # 持仓比例（超出范围时截断到 ±1）
        if max_pos_usd > 0:
            inventory_ratio = max(-1.0, min(1.0, position_usd / max_pos_usd))
        else:
            inventory_ratio = 0.0

        reservation_mid = self._calc_reservation_mid(mid, inventory_ratio)
        half_spread = self.config.spread_bps / 2 / 10_000

        bid_price: Optional[float] = reservation_mid * (1.0 - half_spread)
        ask_price: Optional[float] = reservation_mid * (1.0 + half_spread)

        # 持仓超过 95% 上限时，停止该方向报价
        STOP_QUOTE_THRESHOLD = 0.95
        if inventory_ratio >= STOP_QUOTE_THRESHOLD:
            bid_price = None
            logger.info(
                f"{self.coin}: 净多头达到 {inventory_ratio:.1%}，暂停买单报价"
            )
        if inventory_ratio <= -STOP_QUOTE_THRESHOLD:
            ask_price = None
            logger.info(
                f"{self.coin}: 净空头达到 {abs(inventory_ratio):.1%}，暂停卖单报价"
            )

        # 精度处理
        if bid_price is not None:
            bid_price = _round_price(bid_price)
        if ask_price is not None:
            ask_price = _round_price(ask_price)

        return bid_price, ask_price

    # ──────────────────────────────────────────
    # 订单生命周期
    # ──────────────────────────────────────────

    def _sync_order_status(self) -> None:
        """
        与交易所同步订单状态。
        若我们跟踪的订单已从未成交列表中消失，视为已成交或已撤销。
        """
        if self.bid_oid is None and self.ask_oid is None:
            return

        open_oids = {
            o["oid"]
            for o in self.client.get_open_orders()
            if o["coin"] == self.coin
        }

        if self.bid_oid is not None and self.bid_oid not in open_oids:
            logger.info(f"{self.coin}: 买单 #{self.bid_oid} 已成交或撤销")
            self.bid_oid = None
            self.detected_fills += 1

        if self.ask_oid is not None and self.ask_oid not in open_oids:
            logger.info(f"{self.coin}: 卖单 #{self.ask_oid} 已成交或撤销")
            self.ask_oid = None
            self.detected_fills += 1

    def _cancel_active_orders(self) -> None:
        """取消当前跟踪的买单和卖单。"""
        if self.bid_oid is not None:
            self.client.cancel_order(self.coin, self.bid_oid)
            self.bid_oid = None
        if self.ask_oid is not None:
            self.client.cancel_order(self.coin, self.ask_oid)
            self.ask_oid = None

    # ──────────────────────────────────────────
    # 主更新循环
    # ──────────────────────────────────────────

    def update_quotes(self) -> bool:
        """
        执行一轮报价更新：
          1. 获取行情 mid 价
          2. 检测订单成交状态
          3. 获取当前持仓
          4. 计算偏斜后的 bid / ask 价格
          5. 撤销旧报价
          6. 挂出新报价

        Returns:
            True = 本轮正常完成，False = 发生错误跳过本轮。
        """
        # 1. 获取 mid 价格
        mid = self.client.get_mid(self.coin)
        if mid is None or mid <= 0:
            logger.error(f"{self.coin}: 无法获取 mid 价格，跳过本轮")
            return False

        # 2. 检测成交
        self._sync_order_status()

        # 3. 获取持仓
        position_coins = self.client.get_position(self.coin)
        position_usd = position_coins * mid

        # 4. 计算报价
        bid_price, ask_price = self.calc_quotes(mid, position_coins)

        # 5. 撤销旧报价（先撤后报，避免重复挂单）
        self._cancel_active_orders()

        # 6. 挂新报价
        if bid_price is not None:
            self.bid_oid = self.client.place_limit_order(
                self.coin,
                is_buy=True,
                size_usd=self.config.order_size_usd,
                price=bid_price,
            )

        if ask_price is not None:
            self.ask_oid = self.client.place_limit_order(
                self.coin,
                is_buy=False,
                size_usd=self.config.order_size_usd,
                price=ask_price,
            )

        self.quote_cycles += 1

        # 日志摘要
        bid_str = f"${bid_price:,.4f}" if bid_price else "停报"
        ask_str = f"${ask_price:,.4f}" if ask_price else "停报"
        inv_ratio = position_usd / self.config.max_position_usd if self.config.max_position_usd else 0
        logger.info(
            f"{self.coin}: mid=${mid:,.4f} | "
            f"持仓={position_usd:+.1f}USD ({inv_ratio:+.1%}) | "
            f"bid={bid_str} ask={ask_str} | "
            f"价差={self.config.spread_bps:.1f}bps"
        )
        return True

    def cancel_all(self) -> None:
        """停机时取消所有该标的的挂单（包括可能遗漏的历史订单）。"""
        self._cancel_active_orders()
        remaining = self.client.cancel_all_orders(self.coin)
        if remaining:
            logger.info(f"{self.coin}: 额外清理了 {remaining} 个遗留订单")
