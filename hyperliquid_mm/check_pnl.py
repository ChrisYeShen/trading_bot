import sys
sys.stdout.reconfigure(encoding="utf-8")

from config import BotConfig
from exchange_client import HyperliquidClient

config = BotConfig.from_env()
client = HyperliquidClient(config.private_key, config.use_testnet)

state = client.get_user_state()
summary = state.get("crossMarginSummary", {})
equity        = float(summary.get("accountValue", 0))
margin_used   = float(summary.get("totalMarginUsed", 0))
total_ntl     = float(summary.get("totalNtlPos", 0))

print("=" * 58)
print("  做市 PnL 快照")
print("=" * 58)
print(f"  当前权益:      ${equity:>12,.2f}")
print(f"  已用保证金:    ${margin_used:>12,.2f}")
print(f"  名义持仓总额:  ${total_ntl:>12,.2f}")
print()

positions = [
    p for p in state.get("assetPositions", [])
    if float(p["position"]["szi"]) != 0
]

total_unrealized = 0.0
if positions:
    print("  持仓明细:")
    for pos_info in positions:
        pos = pos_info["position"]
        szi        = float(pos["szi"])
        entry_px   = float(pos.get("entryPx", 0) or 0)
        unrealized = float(pos.get("unrealizedPnl", 0) or 0)
        pos_val    = float(pos.get("positionValue", 0) or 0)
        total_unrealized += unrealized
        side = "多" if szi > 0 else "空"
        coin = pos["coin"]
        print(f"    {coin:<5} {side}  {abs(szi):.4f} 枚"
              f"  均价 ${entry_px:>10,.2f}"
              f"  名义 ${pos_val:>8,.1f}"
              f"  未实现 ${unrealized:>+8,.2f}")
else:
    print("  当前无持仓")

print()
print(f"  未实现总盈亏:  ${total_unrealized:>+12,.2f}")

# 从 bot.log 里统计成交次数
fills = 0
try:
    with open("bot.log", encoding="utf-8") as f:
        for line in f:
            if "已成交或撤销" in line:
                fills += 1
    print(f"  检测到成交次数: {fills} 次（来自 bot.log）")
except FileNotFoundError:
    pass

print("=" * 58)
