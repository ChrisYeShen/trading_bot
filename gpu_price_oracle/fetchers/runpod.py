"""
RunPod rental price fetcher via GraphQL API.
"""
import logging
from typing import Optional

import aiohttp

from .base import GPURentalQuote, RentalFetcher

logger = logging.getLogger(__name__)

RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql"

# Map canonical GPU names → RunPod displayName substrings
GPU_NAME_PATTERNS: dict[str, list[str]] = {
    "NVIDIA RTX 4090":    ["4090"],
    "NVIDIA RTX 3090":    ["3090"],
    "NVIDIA A100 80GB":   ["A100", "80GB"],
    "NVIDIA H100 80GB":   ["H100", "80GB"],
    "NVIDIA A10G":        ["A10G"],
    "NVIDIA L40S":        ["L40S"],
    "AMD RX 7900 XTX":   ["7900 XTX"],
}

_QUERY = """
query GpuTypes {
  gpuTypes {
    id
    displayName
    memoryInGb
    securePrice
    communityPrice
    secureSpotPrice
    communitySpotPrice
  }
}
"""


def _matches(display: str, patterns: list[str]) -> bool:
    return all(p.upper() in display.upper() for p in patterns)


class RunPodFetcher(RentalFetcher):
    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key

    async def fetch(self, gpu_names: list[str]) -> list[GPURentalQuote]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    RUNPOD_GRAPHQL_URL,
                    json={"query": _QUERY},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
        except Exception as exc:
            logger.warning("RunPod fetch failed: %s", exc)
            return []

        gpu_types = data.get("data", {}).get("gpuTypes", [])
        results: list[GPURentalQuote] = []

        for canonical in gpu_names:
            patterns = GPU_NAME_PATTERNS.get(canonical, [canonical.split()[-1]])
            matching = [g for g in gpu_types if _matches(g.get("displayName", ""), patterns)]
            if not matching:
                continue

            prices = []
            for g in matching:
                # prefer community spot (cheapest), fallback to secure
                for field in ("communitySpotPrice", "communityPrice", "secureSpotPrice", "securePrice"):
                    v = g.get(field)
                    if v is not None and float(v) > 0:
                        prices.append(float(v))
                        break

            if prices:
                results.append(
                    GPURentalQuote(
                        gpu_name=canonical,
                        price_usd_per_hour=min(prices),
                        source="runpod",
                        num_listings=len(matching),
                    )
                )

        return results
