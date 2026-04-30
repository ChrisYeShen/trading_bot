"""
Aggregates rental and hardware quotes from multiple fetchers into a single
price per GPU, with outlier rejection and weighted averaging.
"""
import asyncio
import logging
import statistics
from dataclasses import dataclass
from typing import Optional

from .fetchers.base import GPUHardwareQuote, GPURentalQuote

logger = logging.getLogger(__name__)


@dataclass
class AggregatedPrice:
    gpu_name: str
    hardware_price_usd: Optional[float]   # None if no data
    rental_price_usd_per_hour: Optional[float]
    hardware_sources: int
    rental_sources: int


def _median_filtered(values: list[float]) -> float:
    """Return a 25% trimmed mean (robust outlier rejection for small samples)."""
    if len(values) <= 2:
        return statistics.mean(values)
    sorted_v = sorted(values)
    trim = max(1, len(sorted_v) // 4)
    inner = sorted_v[trim:-trim] if len(sorted_v) > 2 * trim else sorted_v
    return statistics.mean(inner)


def aggregate_rental(quotes: list[GPURentalQuote], gpu_name: str) -> tuple[Optional[float], int]:
    relevant = [q for q in quotes if q.gpu_name == gpu_name]
    if not relevant:
        return None, 0
    prices = [q.price_usd_per_hour for q in relevant]
    return _median_filtered(prices), len(relevant)


def aggregate_hardware(quotes: list[GPUHardwareQuote], gpu_name: str) -> tuple[Optional[float], int]:
    relevant = [q for q in quotes if q.gpu_name == gpu_name]
    if not relevant:
        return None, 0
    prices = [q.price_usd for q in relevant]
    return _median_filtered(prices), len(relevant)


def aggregate_all(
    rental_quotes: list[GPURentalQuote],
    hardware_quotes: list[GPUHardwareQuote],
    gpu_names: list[str],
) -> list[AggregatedPrice]:
    results = []
    for name in gpu_names:
        hw_price, hw_src = aggregate_hardware(hardware_quotes, name)
        rent_price, rent_src = aggregate_rental(rental_quotes, name)
        if hw_price is None and rent_price is None:
            logger.warning("No price data for %s, skipping", name)
            continue
        results.append(
            AggregatedPrice(
                gpu_name=name,
                hardware_price_usd=hw_price,
                rental_price_usd_per_hour=rent_price,
                hardware_sources=hw_src,
                rental_sources=rent_src,
            )
        )
    return results
