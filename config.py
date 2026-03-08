"""
config.py — 配置加载模块

从环境变量（.env 文件）读取所有配置参数，
构建 BotConfig 和 SymbolConfig 数据类供其他模块使用。
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class SymbolConfig:
    """单个交易标的的做市参数。"""

    symbol: str
    """交易标的，例如 "BTC"、"ETH"、"SOL"。"""

    spread_bps: float
    """双边报价价差（基点）。5.0 = 0.05%，即 bid/ask 各偏离 mid 2.5bps。"""

    order_size_usd: float
    """每侧挂单金额（USD）。实际挂单数量 = order_size_usd / mid_price。"""

    max_position_usd: float
    """最大允许净持仓（USD 绝对值）。超过此值单侧停止报价。"""


@dataclass
class BotConfig:
    """机器人全局配置。"""

    private_key: str
    """Ethereum 钱包私钥，用于在 Hyperliquid 上签署订单。"""

    use_testnet: bool = False
    """True = 使用 Hyperliquid 测试网，False = 主网。"""

    symbols: List[SymbolConfig] = field(default_factory=list)
    """需要做市的标的列表。"""

    quote_refresh_interval: float = 5.0
    """报价刷新间隔（秒）。每 N 秒取消旧报价并重新报价一次。"""

    max_drawdown_pct: float = 10.0
    """最大允许回撤（%）。触达此值时机器人自动停止并撤单。"""

    portfolio_log_every: int = 12
    """每 N 轮循环打印一次持仓概览。"""

    @classmethod
    def from_env(cls) -> "BotConfig":
        """从环境变量（.env 文件）构建配置。"""
        private_key = os.environ.get("PRIVATE_KEY", "").strip()
        if not private_key:
            raise ValueError(
                "PRIVATE_KEY 未设置。请复制 .env.example 为 .env 并填写你的私钥。"
            )

        use_testnet = os.environ.get("USE_TESTNET", "false").lower() == "true"

        symbols = [
            SymbolConfig(
                symbol="BTC",
                spread_bps=float(os.environ.get("BTC_SPREAD_BPS", "5.0")),
                order_size_usd=float(os.environ.get("BTC_ORDER_SIZE_USD", "200.0")),
                max_position_usd=float(os.environ.get("MAX_POSITION_USD_BTC", "5000.0")),
            ),
            SymbolConfig(
                symbol="ETH",
                spread_bps=float(os.environ.get("ETH_SPREAD_BPS", "5.0")),
                order_size_usd=float(os.environ.get("ETH_ORDER_SIZE_USD", "200.0")),
                max_position_usd=float(os.environ.get("MAX_POSITION_USD_ETH", "3000.0")),
            ),
            SymbolConfig(
                symbol="SOL",
                spread_bps=float(os.environ.get("SOL_SPREAD_BPS", "8.0")),
                order_size_usd=float(os.environ.get("SOL_ORDER_SIZE_USD", "200.0")),
                max_position_usd=float(os.environ.get("MAX_POSITION_USD_SOL", "2000.0")),
            ),
        ]

        return cls(
            private_key=private_key,
            use_testnet=use_testnet,
            symbols=symbols,
            quote_refresh_interval=float(
                os.environ.get("QUOTE_REFRESH_INTERVAL", "5.0")
            ),
            max_drawdown_pct=float(os.environ.get("MAX_DRAWDOWN_PCT", "10.0")),
            portfolio_log_every=int(os.environ.get("PORTFOLIO_LOG_EVERY", "12")),
        )
