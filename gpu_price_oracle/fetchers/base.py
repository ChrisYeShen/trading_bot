from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class GPURentalQuote:
    gpu_name: str           # canonical name
    price_usd_per_hour: float
    source: str
    num_listings: int = 1   # how many listings were averaged


@dataclass
class GPUHardwareQuote:
    gpu_name: str
    price_usd: float
    source: str
    num_listings: int = 1


class RentalFetcher(ABC):
    @abstractmethod
    async def fetch(self, gpu_names: list[str]) -> list[GPURentalQuote]:
        """Return rental quotes for each requested GPU model."""


class HardwareFetcherBase(ABC):
    @abstractmethod
    async def fetch(self, gpu_names: list[str]) -> list[GPUHardwareQuote]:
        """Return hardware sale quotes for each requested GPU model."""
