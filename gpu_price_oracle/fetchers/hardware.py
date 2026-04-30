"""
GPU hardware price fetcher.
Queries the eBay Finding API (public sandbox) for used/new GPU listings
and the TechPowerUp GPU database for MSRP reference prices.

For production use, replace the eBay integration with an authenticated
eBay Developer API key and supplement with Amazon Product API / Newegg.
"""
import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp

from .base import GPUHardwareQuote, HardwareFetcherBase

logger = logging.getLogger(__name__)

# Static MSRP fallback table (USD) sourced from manufacturer launch prices.
# These are used when live market data is unavailable.
MSRP_TABLE: dict[str, float] = {
    "NVIDIA RTX 4090":  1599.0,
    "NVIDIA RTX 3090":   999.0,
    "NVIDIA A100 80GB": 10000.0,
    "NVIDIA H100 80GB": 30000.0,
    "NVIDIA A10G":       3500.0,
    "NVIDIA L40S":       7000.0,
    "AMD RX 7900 XTX":    999.0,
}

# eBay search terms per canonical GPU
EBAY_SEARCH_TERMS: dict[str, str] = {
    "NVIDIA RTX 4090":    "RTX 4090 GPU",
    "NVIDIA RTX 3090":    "RTX 3090 GPU",
    "NVIDIA A100 80GB":   "A100 80GB GPU",
    "NVIDIA H100 80GB":   "H100 80GB GPU",
    "NVIDIA A10G":        "Nvidia A10G GPU",
    "NVIDIA L40S":        "Nvidia L40S GPU",
    "AMD RX 7900 XTX":   "RX 7900 XTX GPU",
}

EBAY_FINDING_URL = "https://svcs.ebay.com/services/search/FindingService/v1"


class HardwarePriceFetcher(HardwareFetcherBase):
    """
    Fetches hardware prices from eBay completed listings.
    Falls back to static MSRP when the API is unavailable or no key is set.
    """

    def __init__(self, ebay_app_id: Optional[str] = None):
        self._ebay_app_id = ebay_app_id

    async def fetch(self, gpu_names: list[str]) -> list[GPUHardwareQuote]:
        if self._ebay_app_id:
            return await self._fetch_ebay(gpu_names)
        return self._msrp_fallback(gpu_names)

    async def _fetch_ebay(self, gpu_names: list[str]) -> list[GPUHardwareQuote]:
        results: list[GPUHardwareQuote] = []
        async with aiohttp.ClientSession() as session:
            for canonical in gpu_names:
                query = EBAY_SEARCH_TERMS.get(canonical, canonical)
                params = {
                    "OPERATION-NAME": "findCompletedItems",
                    "SERVICE-VERSION": "1.0.0",
                    "SECURITY-APPNAME": self._ebay_app_id,
                    "RESPONSE-DATA-FORMAT": "JSON",
                    "keywords": query,
                    "itemFilter(0).name": "SoldItemsOnly",
                    "itemFilter(0).value": "true",
                    "itemFilter(1).name": "Condition",
                    "itemFilter(1).value": "3000",  # used
                    "sortOrder": "EndTimeSoonest",
                    "paginationInput.entriesPerPage": "50",
                }
                try:
                    async with session.get(
                        EBAY_FINDING_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                    items = (
                        data.get("findCompletedItemsResponse", [{}])[0]
                        .get("searchResult", [{}])[0]
                        .get("item", [])
                    )
                    prices = [
                        float(item["sellingStatus"][0]["convertedCurrentPrice"][0]["__value__"])
                        for item in items
                        if item.get("sellingStatus")
                    ]
                    if prices:
                        prices.sort()
                        mid = prices[len(prices) // 4 : 3 * len(prices) // 4]
                        avg = sum(mid) / len(mid)
                        results.append(
                            GPUHardwareQuote(
                                gpu_name=canonical,
                                price_usd=avg,
                                source="ebay_completed",
                                num_listings=len(prices),
                            )
                        )
                    else:
                        results.extend(self._msrp_fallback([canonical]))
                except Exception as exc:
                    logger.warning("eBay fetch failed for %s: %s", canonical, exc)
                    results.extend(self._msrp_fallback([canonical]))

        return results

    def _msrp_fallback(self, gpu_names: list[str]) -> list[GPUHardwareQuote]:
        return [
            GPUHardwareQuote(
                gpu_name=name,
                price_usd=MSRP_TABLE.get(name, 0.0),
                source="msrp_static",
                num_listings=1,
            )
            for name in gpu_names
            if MSRP_TABLE.get(name, 0.0) > 0
        ]
