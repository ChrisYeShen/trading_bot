"""
polymarket_client.py — Polymarket CLOB API 封装
使用 py-clob-client (>=0.34.0)

功能：
  - L1/L2 身份验证
  - 扫描活跃市场（支持关键词过滤）
  - 获取订单簿（最优买卖价）
  - 创建并提交限价做市单（GTC）
  - 查询当前挂单 / 取消订单
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    OrderArgs,
    OrderType,
)
from py_clob_client.constants import POLYGON
from py_clob_client.exceptions import PolyApiException

logger = logging.getLogger(__name__)

POLY_HOST = "https://clob.polymarket.com"
CHAIN_ID  = POLYGON   # 137


@dataclass
class MarketInfo:
    condition_id:   str
    question:       str
    description:    str
    yes_token_id:   str
    no_token_id:    str
    accepting_orders: bool
    # 最优价（从订单簿实时获取后填充）
    yes_best_ask:   Optional[float] = None   # 买 YES 需支付
    yes_best_bid:   Optional[float] = None   # 卖 YES 可得到
    no_best_ask:    Optional[float] = None
    no_best_bid:    Optional[float] = None

    @property
    def yes_mid(self) -> Optional[float]:
        if self.yes_best_ask and self.yes_best_bid:
            return (self.yes_best_ask + self.yes_best_bid) / 2
        return self.yes_best_ask or self.yes_best_bid

    @property
    def implied_yes_prob(self) -> Optional[float]:
        """YES mid price 即为隐含 YES 概率"""
        return self.yes_mid


@dataclass
class OrderResult:
    success:      bool
    order_id:     str = ""
    error_msg:    str = ""
    side:         str = ""   # "YES" | "NO"
    price:        float = 0.0
    size:         float = 0.0


class PolymarketClient:
    def __init__(self, private_key: str, host: str = POLY_HOST):
        self._key  = private_key
        self._host = host
        self._client: Optional[ClobClient] = None
        self._creds: Optional[ApiCreds]    = None
        self._connect()

    # ── 连接 & 认证 ───────────────────────────────────────────

    def _connect(self):
        """建立 L1 连接并导出 L2 API 凭证"""
        try:
            self._client = ClobClient(
                host     = self._host,
                key      = self._key,
                chain_id = CHAIN_ID,
            )
            # 派生 L2 凭证（apiKey + secret + passphrase）
            self._creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(self._creds)
            logger.info("Polymarket CLOB 连接成功")
        except Exception as e:
            logger.error(f"Polymarket 连接失败: {e}")
            raise

    def _ensure_connected(self):
        if self._client is None:
            self._connect()

    # ── 市场扫描 ──────────────────────────────────────────────

    def get_markets(
        self,
        keywords: list[str] | None = None,
        limit: int = 200,
    ) -> list[MarketInfo]:
        """
        获取活跃市场列表，可按关键词过滤（大小写不敏感）。
        """
        self._ensure_connected()
        markets: list[MarketInfo] = []
        next_cursor = ""

        while True:
            try:
                resp = self._client.get_markets(
                    next_cursor=next_cursor,
                )
            except PolyApiException as e:
                logger.error(f"get_markets 失败: {e}")
                break

            for m in resp.get("data", []):
                if not m.get("active") or m.get("closed"):
                    continue
                if not m.get("accepting_orders"):
                    continue

                question    = m.get("question", "")
                description = m.get("description", "")

                # 关键词过滤
                if keywords:
                    text = (question + " " + description).lower()
                    if not any(kw.lower() in text for kw in keywords):
                        continue

                # 提取 YES / NO token IDs
                tokens = m.get("tokens", [])
                yes_id = no_id = ""
                for tok in tokens:
                    if tok.get("outcome", "").upper() == "YES":
                        yes_id = tok.get("token_id", "")
                    elif tok.get("outcome", "").upper() == "NO":
                        no_id = tok.get("token_id", "")

                if not yes_id:
                    continue

                markets.append(MarketInfo(
                    condition_id    = m.get("condition_id", ""),
                    question        = question,
                    description     = description,
                    yes_token_id    = yes_id,
                    no_token_id     = no_id,
                    accepting_orders = True,
                ))

            next_cursor = resp.get("next_cursor", "")
            if not next_cursor or next_cursor == "LTE=" or len(markets) >= limit:
                break

        logger.info(f"扫描到 {len(markets)} 个符合条件的活跃市场")
        return markets[:limit]

    # ── 订单簿 ────────────────────────────────────────────────

    def enrich_with_orderbook(self, market: MarketInfo) -> MarketInfo:
        """
        填充 market 的最优买/卖价（就地修改并返回）。
        """
        self._ensure_connected()
        try:
            book = self._client.get_order_book(market.yes_token_id)
            bids = book.bids or []
            asks = book.asks or []

            market.yes_best_bid = float(bids[0].price)  if bids else None
            market.yes_best_ask = float(asks[0].price)  if asks else None

            # NO token 价格 ≈ 1 - YES price（Polymarket 保证 YES+NO=1）
            if market.yes_best_bid:
                market.no_best_ask = round(1.0 - market.yes_best_bid, 4)
            if market.yes_best_ask:
                market.no_best_bid = round(1.0 - market.yes_best_ask, 4)

        except Exception as e:
            logger.debug(f"订单簿获取失败 [{market.condition_id[:8]}]: {e}")
        return market

    # ── 下单 ─────────────────────────────────────────────────

    def place_limit_order(
        self,
        token_id: str,
        side: str,          # "BUY" | "SELL"
        price: float,       # 0.01 ~ 0.99
        size: float,        # USDC 数量
        dry_run: bool = False,
    ) -> OrderResult:
        """
        提交 GTC 限价单。
        side="BUY"  → 买入 YES（看涨）
        side="SELL" → 卖出 YES（看跌，等价于买 NO）
        dry_run=True 时只打印，不真实下单。
        """
        price = round(max(0.01, min(0.99, price)), 4)
        size  = round(size, 2)

        label = "YES" if side == "BUY" else "NO"
        logger.info(
            f"[{'DRY' if dry_run else 'LIVE'}] {side} {label} "
            f"token={token_id[:10]}… price={price} size={size} USDC"
        )

        if dry_run:
            return OrderResult(
                success  = True,
                order_id = "DRY_RUN",
                side     = label,
                price    = price,
                size     = size,
            )

        self._ensure_connected()
        try:
            order_args = OrderArgs(
                token_id = token_id,
                price    = price,
                size     = size,
                side     = side,
            )
            signed = self._client.create_order(order_args)
            resp   = self._client.post_order(signed, OrderType.GTC)
            order_id = resp.get("orderID", resp.get("id", "unknown"))
            logger.info(f"下单成功: order_id={order_id}")
            return OrderResult(
                success  = True,
                order_id = order_id,
                side     = label,
                price    = price,
                size     = size,
            )
        except PolyApiException as e:
            logger.error(f"Polymarket 下单失败: {e}")
            return OrderResult(success=False, error_msg=str(e))
        except Exception as e:
            logger.error(f"下单异常: {e}")
            return OrderResult(success=False, error_msg=str(e))

    # ── 订单管理 ──────────────────────────────────────────────

    def get_open_orders(self) -> list[dict]:
        """获取当前所有挂单"""
        self._ensure_connected()
        try:
            resp = self._client.get_orders()
            return resp.get("data", []) if isinstance(resp, dict) else resp
        except Exception as e:
            logger.error(f"获取挂单失败: {e}")
            return []

    def cancel_order(self, order_id: str) -> bool:
        """取消单个挂单"""
        self._ensure_connected()
        try:
            self._client.cancel(order_id)
            logger.info(f"撤单成功: {order_id}")
            return True
        except Exception as e:
            logger.error(f"撤单失败 {order_id}: {e}")
            return False

    def cancel_all_orders(self) -> int:
        """取消所有挂单，返回成功撤销数量"""
        orders = self.get_open_orders()
        cancelled = 0
        for o in orders:
            oid = o.get("id", o.get("orderID", ""))
            if oid and self.cancel_order(oid):
                cancelled += 1
        logger.info(f"共撤销 {cancelled} 个挂单")
        return cancelled
