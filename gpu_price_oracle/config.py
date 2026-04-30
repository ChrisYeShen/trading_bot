import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    # ── Blockchain ───────────────────────────────────────────────────────────
    rpc_url: str = os.getenv("RPC_URL", "http://localhost:8545")
    chain_id: int = int(os.getenv("CHAIN_ID", "1"))
    oracle_contract_address: str = os.getenv("ORACLE_CONTRACT_ADDRESS", "")
    feeder_private_key: str = os.getenv("FEEDER_PRIVATE_KEY", "")

    # ── Data sources ─────────────────────────────────────────────────────────
    vast_ai_api_key: Optional[str] = os.getenv("VAST_AI_API_KEY")
    runpod_api_key: Optional[str] = os.getenv("RUNPOD_API_KEY")
    lambda_labs_api_key: Optional[str] = os.getenv("LAMBDA_LABS_API_KEY")

    # ── Oracle behaviour ─────────────────────────────────────────────────────
    update_interval_seconds: int = int(os.getenv("UPDATE_INTERVAL_SECONDS", "3600"))
    # Reject a price if it deviates > X% from the previous on-chain value
    max_price_deviation_pct: float = float(os.getenv("MAX_PRICE_DEVIATION_PCT", "30.0"))

    # GPU models to track (canonical names)
    tracked_gpus: list = field(default_factory=lambda: [
        "NVIDIA RTX 4090",
        "NVIDIA RTX 3090",
        "NVIDIA A100 80GB",
        "NVIDIA H100 80GB",
        "NVIDIA A10G",
        "NVIDIA L40S",
        "AMD RX 7900 XTX",
    ])

    # Gas settings
    gas_limit: int = int(os.getenv("GAS_LIMIT", "500000"))
    max_fee_per_gas_gwei: Optional[float] = (
        float(os.getenv("MAX_FEE_PER_GAS_GWEI")) if os.getenv("MAX_FEE_PER_GAS_GWEI") else None
    )


def load_config() -> Config:
    return Config()
