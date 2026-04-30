"""
Deploy GPUPriceOracle to an EVM chain.

Usage:
    python -m gpu_price_oracle.scripts.deploy

Requires: solc + py-solc-x, or a pre-compiled ABI/bytecode in artifacts/.
Set RPC_URL, FEEDER_PRIVATE_KEY, CHAIN_ID in your .env before running.
"""
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ARTIFACTS_DIR = Path(__file__).parent.parent / "artifacts"

def load_artifact(name: str) -> tuple[list, str]:
    abi_path = ARTIFACTS_DIR / f"{name}.abi.json"
    bin_path = ARTIFACTS_DIR / f"{name}.bin"
    if not abi_path.exists() or not bin_path.exists():
        logger.error(
            "Compiled artifacts not found at %s. "
            "Run: solc --abi --bin contracts/GPUPriceOracle.sol -o artifacts/",
            ARTIFACTS_DIR,
        )
        sys.exit(1)
    abi = json.loads(abi_path.read_text())
    bytecode = bin_path.read_text().strip()
    return abi, bytecode


def main():
    rpc_url = os.environ["RPC_URL"]
    private_key = os.environ["FEEDER_PRIVATE_KEY"]
    chain_id = int(os.environ.get("CHAIN_ID", "1"))

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    assert w3.is_connected(), f"Cannot connect to {rpc_url}"

    account = w3.eth.account.from_key(private_key)
    logger.info("Deploying from %s on chain %d", account.address, chain_id)

    abi, bytecode = load_artifact("GPUPriceOracle")
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)

    tx = contract.constructor().build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 2_000_000,
            "chainId": chain_id,
            "gasPrice": w3.eth.gas_price,
        }
    )
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    logger.info("Tx sent: %s  – waiting for receipt…", tx_hash.hex())
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

    if receipt["status"] != 1:
        logger.error("Deployment reverted!")
        sys.exit(1)

    address = receipt["contractAddress"]
    logger.info("GPUPriceOracle deployed at: %s", address)
    print(f"\nORACLE_CONTRACT_ADDRESS={address}")

    # Save for convenience
    (ARTIFACTS_DIR / "deployed_address.txt").write_text(address)


if __name__ == "__main__":
    main()
