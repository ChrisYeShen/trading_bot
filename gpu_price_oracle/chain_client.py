"""
Web3 client for submitting price updates to the GPUPriceOracle contract.
"""
import json
import logging
import math
import os
from pathlib import Path
from typing import Optional

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from .aggregator import AggregatedPrice
from .config import Config

logger = logging.getLogger(__name__)

# Minimal ABI – only what the oracle publisher needs
ORACLE_ABI = json.loads("""
[
  {
    "inputs": [
      {"internalType": "string[]", "name": "gpuNames", "type": "string[]"},
      {"internalType": "uint256[]", "name": "hardwareUsdCents", "type": "uint256[]"},
      {"internalType": "uint256[]", "name": "rentalMilliCents", "type": "uint256[]"},
      {"internalType": "uint256[]", "name": "numSourcesArr", "type": "uint256[]"}
    ],
    "name": "updatePriceBatch",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
  },
  {
    "inputs": [
      {"internalType": "string", "name": "gpuName", "type": "string"}
    ],
    "name": "getPrice",
    "outputs": [
      {"internalType": "uint256", "name": "hardwarePriceUsdCents", "type": "uint256"},
      {"internalType": "uint256", "name": "rentalPriceUsdMilliCentsPerHour", "type": "uint256"},
      {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
      {"internalType": "uint256", "name": "numSources", "type": "uint256"}
    ],
    "stateMutability": "view",
    "type": "function"
  }
]
""")


def _usd_to_cents(usd: Optional[float]) -> int:
    if usd is None:
        return 0
    return max(0, math.floor(usd * 100))


def _usd_per_hour_to_milli_cents(usd_per_hour: Optional[float]) -> int:
    """Convert $/hr to USD milli-cents per hour (1 $ = 100 cents = 100,000 milli-cents)."""
    if usd_per_hour is None:
        return 0
    return max(0, math.floor(usd_per_hour * 100_000))


class ChainClient:
    def __init__(self, config: Config):
        self._cfg = config
        self._w3 = Web3(Web3.HTTPProvider(config.rpc_url))
        # PoA chains (Polygon, BSC, etc.) need this middleware
        self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        if not self._w3.is_connected():
            raise ConnectionError(f"Cannot connect to RPC: {config.rpc_url}")

        self._account = self._w3.eth.account.from_key(config.feeder_private_key)
        self._contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(config.oracle_contract_address),
            abi=ORACLE_ABI,
        )
        logger.info("Connected to chain %s as %s", config.chain_id, self._account.address)

    def get_on_chain_price(self, gpu_name: str) -> Optional[dict]:
        """Read the latest on-chain price for a GPU (returns None if not yet set)."""
        try:
            hw, rent, updated_at, sources = self._contract.functions.getPrice(gpu_name).call()
            return {
                "hardware_usd_cents": hw,
                "rental_milli_cents": rent,
                "updated_at": updated_at,
                "num_sources": sources,
            }
        except Exception:
            return None

    def _is_deviation_safe(self, new_usd: float, field: str, on_chain: Optional[dict]) -> bool:
        if on_chain is None:
            return True
        if field == "hardware":
            old_cents = on_chain["hardware_usd_cents"]
            new_cents = _usd_to_cents(new_usd)
        else:
            old_cents = on_chain["rental_milli_cents"]
            new_cents = _usd_per_hour_to_milli_cents(new_usd)
        if old_cents == 0:
            return True
        pct = abs(new_cents - old_cents) / old_cents * 100
        if pct > self._cfg.max_price_deviation_pct:
            logger.warning(
                "Price deviation %.1f%% exceeds limit %.1f%% for %s (%s)",
                pct,
                self._cfg.max_price_deviation_pct,
                field,
                new_usd,
            )
            return False
        return True

    def submit_batch(self, prices: list[AggregatedPrice]) -> Optional[str]:
        """Build and send a batch price update transaction. Returns tx hash or None."""
        if not prices:
            return None

        gpu_names, hw_cents, rent_milli, num_sources = [], [], [], []

        for p in prices:
            on_chain = self.get_on_chain_price(p.gpu_name)

            # Deviation guard
            if p.hardware_price_usd is not None and not self._is_deviation_safe(
                p.hardware_price_usd, "hardware", on_chain
            ):
                logger.warning("Skipping %s due to hardware price deviation", p.gpu_name)
                continue
            if p.rental_price_usd_per_hour is not None and not self._is_deviation_safe(
                p.rental_price_usd_per_hour, "rental", on_chain
            ):
                logger.warning("Skipping %s due to rental price deviation", p.gpu_name)
                continue

            gpu_names.append(p.gpu_name)
            hw_cents.append(_usd_to_cents(p.hardware_price_usd))
            rent_milli.append(_usd_per_hour_to_milli_cents(p.rental_price_usd_per_hour))
            num_sources.append(p.hardware_sources + p.rental_sources)

        if not gpu_names:
            logger.info("No prices passed deviation check – nothing to submit")
            return None

        nonce = self._w3.eth.get_transaction_count(self._account.address)
        tx_params: dict = {
            "from": self._account.address,
            "nonce": nonce,
            "gas": self._cfg.gas_limit,
            "chainId": self._cfg.chain_id,
        }

        if self._cfg.max_fee_per_gas_gwei:
            max_fee = Web3.to_wei(self._cfg.max_fee_per_gas_gwei, "gwei")
            tx_params["maxFeePerGas"] = max_fee
            tx_params["maxPriorityFeePerGas"] = Web3.to_wei(1, "gwei")
        else:
            tx_params["gasPrice"] = self._w3.eth.gas_price

        tx = self._contract.functions.updatePriceBatch(
            gpu_names, hw_cents, rent_milli, num_sources
        ).build_transaction(tx_params)

        signed = self._account.sign_transaction(tx)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        if receipt["status"] != 1:
            raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")

        logger.info(
            "Submitted %d GPU prices in tx %s (block %s)",
            len(gpu_names),
            tx_hash.hex(),
            receipt["blockNumber"],
        )
        return tx_hash.hex()
