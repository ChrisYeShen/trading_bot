"""
vast.ai rental price fetcher.
API docs: https://vast.ai/docs/gpu-instances/search
"""
import asyncio
import logging
import re
from typing import Optional

import aiohttp

from .base import GPURentalQuote, RentalFetcher

logger = logging.getLogger(__name__)

VAST_SEARCH_URL = "https://console.vast.ai/api/v0/bundles/"

# Map canonical GPU names → substrings that appear in vast.ai gpu_name field
GPU_NAME_PATTERNS: dict[str, list[str]] = {
    "NVIDIA RTX 4090":    ["4090"],
    "NVIDIA RTX 3090":    ["3090"],
    "NVIDIA A100 80GB":   ["A100", "80"],
    "NVIDIA H100 80GB":   ["H100", "80"],
    "NVIDIA A10G":        ["A10G"],
    "NVIDIA L40S":        ["L40S"],
    "AMD RX 7900 XTX":   ["7900 XTX", "7900XTX"],
}


def _matches(vast_name: str, patterns: list[str]) -> bool:
    return all(p.upper() in vast_name.upper() for p in patterns)


class VastAIFetcher(RentalFetcher):
    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key

    async def fetch(self, gpu_names: list[str]) -> list[GPURentalQuote]:
        params = {
            "q": {
                "rentable": {"eq": True},
                "num_gpus": {"eq": 1},
                "order": [["dph_total", "asc"]],
                "limit": 500,
            }
        }
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    VAST_SEARCH_URL, json=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception as exc:
            logger.warning("vast.ai fetch failed: %s", exc)
            return []

        offers = data.get("offers", [])
        results: list[GPURentalQuote] = []

        for canonical in gpu_names:
            patterns = GPU_NAME_PATTERNS.get(canonical, [canonical.split()[-1]])
            matching = [
                o for o in offers
                if _matches(o.get("gpu_name", ""), patterns)
            ]
            if not matching:
                continue
            prices = [o["dph_total"] for o in matching if o.get("dph_total") is not None]
            if prices:
                # use median of lowest quartile to approximate typical spot price
                prices.sort()
                cutoff = max(1, len(prices) // 4)
                avg = sum(prices[:cutoff]) / cutoff
                results.append(
                    GPURentalQuote(
                        gpu_name=canonical,
                        price_usd_per_hour=avg,
                        source="vast.ai",
                        num_listings=len(prices),
                    )
                )

        return results
