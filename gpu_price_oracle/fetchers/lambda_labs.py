"""
Lambda Labs GPU Cloud rental price fetcher.
API: https://cloud.lambdalabs.com/api/v1/instance-types
"""
import logging
from typing import Optional

import aiohttp

from .base import GPURentalQuote, RentalFetcher

logger = logging.getLogger(__name__)

LAMBDA_INSTANCE_TYPES_URL = "https://cloud.lambdalabs.com/api/v1/instance-types"

# Map canonical GPU name → Lambda Labs instance-type name substrings
GPU_NAME_PATTERNS: dict[str, list[str]] = {
    "NVIDIA RTX 4090":    ["rtx_4090", "4090"],
    "NVIDIA RTX 3090":    ["rtx_3090", "3090"],
    "NVIDIA A100 80GB":   ["a100", "80gb"],
    "NVIDIA H100 80GB":   ["h100", "80gb"],
    "NVIDIA A10G":        ["a10"],
    "NVIDIA L40S":        ["l40s"],
}


def _matches(name: str, patterns: list[str]) -> bool:
    return all(p.lower() in name.lower() for p in patterns)


class LambdaLabsFetcher(RentalFetcher):
    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key

    async def fetch(self, gpu_names: list[str]) -> list[GPURentalQuote]:
        if not self._api_key:
            logger.debug("Lambda Labs API key not set, skipping")
            return []

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    LAMBDA_INSTANCE_TYPES_URL,
                    auth=aiohttp.BasicAuth(self._api_key, ""),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception as exc:
            logger.warning("Lambda Labs fetch failed: %s", exc)
            return []

        instance_types = data.get("data", {})
        results: list[GPURentalQuote] = []

        for canonical in gpu_names:
            patterns = GPU_NAME_PATTERNS.get(canonical, [canonical.replace(" ", "_").lower()])
            matching = {
                k: v for k, v in instance_types.items()
                if _matches(k, patterns)
            }
            if not matching:
                continue

            prices = []
            for _, info in matching.items():
                price_cents = info.get("instance_type", {}).get("price_cents_per_hour")
                if price_cents is not None:
                    prices.append(price_cents / 100.0)

            if prices:
                results.append(
                    GPURentalQuote(
                        gpu_name=canonical,
                        price_usd_per_hour=min(prices),
                        source="lambda_labs",
                        num_listings=len(prices),
                    )
                )

        return results
