"""
exchange_client.py — Hyperliquid SDK 封装

封装 hyperliquid-python-sdk 的常用操作：
- 下单 / 取消订单
- 查询持仓 / 账户净值
- 查询实时报价 / 订单簿

对外暴露简洁、带错误处理的接口，屏蔽 SDK 内部细节。
"""

import logging
from typing import Dict, List, Optional

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

logger = logging.getLogger(__name__)


class HyperliquidClient:
    """Hyperliquid 交易所客户端。"""

    def __init__(self, private_key: str, use_testnet: bool = False):
        self.account = eth_account.Account.from_key(private_key)
        self.address: str = self.account.address

        base_url = (
            constants.TESTNET_API_URL if use_testnet else constants.MAINNET_API_URL
        )

        # skip_ws=True：不启动 WebSocket（轮询模式即可，降低复杂度）
        self.info = Info(base_url, skip_ws=True)
        self.exchange = Exchange(self.account, base_url)

        # 缓存各标的的合约数量精度（小数位数）
        self._sz_decimals: Dict[str, int] = {}
        self._load_asset_meta()

        network_name = "测试网" if use_testnet else "主网"
        logger.info(f"已连接 Hyperliquid {network_name}，地址: {self.address}")

    # ──────────────────────────────────────────
    # 初始化 & 元数据
    # ──────────────────────────────────────────

    def _load_asset_meta(self) -> None:
        """加载并缓存合约精度信息。"""
        try:
            meta = self.info.meta()
            for asset in meta["universe"]:
                self._sz_decimals[asset["name"]] = asset.get("szDecimals", 4)
            logger.info(f"已加载 {len(self._sz_decimals)} 个合约的精度信息")
        except Exception as exc:
            logger.error(f"加载合约元数据失败，使用默认精度: {exc}")
            # 后备默认值
            self._sz_decimals = {"BTC": 5, "ETH": 4, "SOL": 2}

    def get_sz_decimals(self, coin: str) -> int:
        """返回指定标的的数量精度（小数位数）。"""
        return self._sz_decimals.get(coin, 4)

    # ──────────────────────────────────────────
    # 行情数据
    # ──────────────────────────────────────────

    def get_all_mids(self) -> Dict[str, float]:
        """获取所有标的的最新 mid 价格。返回 {coin: price}。"""
        try:
            raw = self.info.all_mids()
            return {k: float(v) for k, v in raw.items()}
        except Exception as exc:
            logger.error(f"获取 mid 价格失败: {exc}")
            return {}

    def get_mid(self, coin: str) -> Optional[float]:
        """获取单个标的的 mid 价格。失败返回 None。"""
        return self.get_all_mids().get(coin)

    def get_best_bid_ask(self, coin: str) -> Optional[tuple[float, float]]:
        """
        获取最优买一 / 卖一价格。
        返回 (best_bid, best_ask) 或 None。
        """
        try:
            book = self.info.l2_snapshot(coin)
            bids = book.get("levels", [[], []])[0]
            asks = book.get("levels", [[], []])[1]
            if not bids or not asks:
                return None
            best_bid = float(bids[0]["px"])
            best_ask = float(asks[0]["px"])
            return best_bid, best_ask
        except Exception as exc:
            logger.error(f"获取 {coin} 订单簿失败: {exc}")
            return None

    # ──────────────────────────────────────────
    # 账户 & 持仓
    # ──────────────────────────────────────────

    def get_user_state(self) -> Optional[Dict]:
        """获取账户完整状态（持仓、余额、保证金等）。"""
        try:
            return self.info.user_state(self.address)
        except Exception as exc:
            logger.error(f"获取账户状态失败: {exc}")
            return None

    def get_position(self, coin: str) -> float:
        """
        获取指定标的的净持仓（以合约单位计）。
        正数 = 多头，负数 = 空头，0 = 无持仓。
        """
        state = self.get_user_state()
        if not state:
            return 0.0
        for pos_info in state.get("assetPositions", []):
            pos = pos_info["position"]
            if pos["coin"] == coin:
                return float(pos["szi"])
        return 0.0

    def get_positions(self) -> Dict[str, float]:
        """获取所有非零持仓。返回 {coin: size_in_contracts}。"""
        state = self.get_user_state()
        if not state:
            return {}
        result = {}
        for pos_info in state.get("assetPositions", []):
            pos = pos_info["position"]
            szi = float(pos["szi"])
            if szi != 0.0:
                result[pos["coin"]] = szi
        return result

    def get_account_value(self) -> float:
        """获取账户总价值（USD）。失败返回 0.0。"""
        state = self.get_user_state()
        if not state:
            return 0.0
        summary = state.get("crossMarginSummary", {})
        return float(summary.get("accountValue", 0.0))

    def get_open_orders(self) -> List[Dict]:
        """获取所有未成交订单。"""
        try:
            return self.info.open_orders(self.address)
        except Exception as exc:
            logger.error(f"获取未成交订单失败: {exc}")
            return []

    # ──────────────────────────────────────────
    # 交易操作
    # ──────────────────────────────────────────

    def place_limit_order(
        self,
        coin: str,
        is_buy: bool,
        size_usd: float,
        price: float,
        reduce_only: bool = False,
    ) -> Optional[int]:
        """
        下 GTC 限价单。成功返回订单 ID (oid)，失败返回 None。

        Args:
            coin:        标的名称，如 "BTC"
            is_buy:      True = 买单，False = 卖单
            size_usd:    名义金额（USD），自动换算为合约数量
            price:       限价价格
            reduce_only: 是否仅减仓
        """
        try:
            sz_decimals = self.get_sz_decimals(coin)
            sz = round(size_usd / price, sz_decimals)

            if sz <= 0:
                logger.warning(f"{coin}: 计算出的下单数量 <= 0，跳过")
                return None

            result = self.exchange.order(
                coin,
                is_buy,
                sz,
                price,
                {"limit": {"tif": "Gtc"}},
                reduce_only=reduce_only,
            )

            if result.get("status") != "ok":
                logger.error(f"{coin}: 下单失败 — {result}")
                return None

            statuses = result["response"]["data"]["statuses"]
            if not statuses:
                return None

            status = statuses[0]
            if "resting" in status:
                oid = status["resting"]["oid"]
                side_str = "买" if is_buy else "卖"
                logger.info(
                    f"{coin}: {side_str}单已挂出 #{oid} — "
                    f"{sz} {coin} @ ${price:,.4f}"
                )
                return oid
            elif "filled" in status:
                # 极少数情况下立即成交（价格穿越）
                side_str = "买" if is_buy else "卖"
                filled_sz = status["filled"].get("totalSz", sz)
                logger.info(f"{coin}: {side_str}单立即成交 {filled_sz} {coin}")
                return None  # 无需跟踪
            else:
                logger.warning(f"{coin}: 意外的订单状态 — {status}")
                return None

        except Exception as exc:
            logger.error(f"{coin}: 下单异常 — {exc}", exc_info=True)
            return None

    def cancel_order(self, coin: str, oid: int) -> bool:
        """取消单个订单。成功返回 True。"""
        try:
            result = self.exchange.cancel(coin, oid)
            if result.get("status") == "ok":
                logger.debug(f"{coin}: 已取消订单 #{oid}")
                return True
            else:
                # 可能订单已成交，非严重错误
                logger.debug(f"{coin}: 取消订单 #{oid} 返回 — {result}")
                return False
        except Exception as exc:
            logger.error(f"{coin}: 取消订单 #{oid} 异常 — {exc}")
            return False

    def cancel_all_orders(self, coin: Optional[str] = None) -> int:
        """
        取消所有（或指定标的的）未成交订单。
        返回成功取消的数量。
        """
        open_orders = self.get_open_orders()
        if coin:
            open_orders = [o for o in open_orders if o["coin"] == coin]

        if not open_orders:
            return 0

        cancels = [{"coin": o["coin"], "oid": o["oid"]} for o in open_orders]

        try:
            result = self.exchange.bulk_cancel(cancels)
            if result.get("status") == "ok":
                logger.info(f"批量取消了 {len(cancels)} 个订单")
                return len(cancels)
            else:
                logger.error(f"批量取消失败 — {result}")
                return 0
        except Exception as exc:
            logger.error(f"批量取消异常 — {exc}")
            return 0
