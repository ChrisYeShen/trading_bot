"""
Main oracle loop: fetch → aggregate → submit on a configurable interval.
"""
import asyncio
import logging
import time

from .aggregator import aggregate_all
from .chain_client import ChainClient
from .config import Config, load_config
from .fetchers import HardwarePriceFetcher, LambdaLabsFetcher, RunPodFetcher, VastAIFetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def run_once(cfg: Config, chain: ChainClient) -> None:
    """Fetch prices from all sources, aggregate, and push to chain."""
    rental_fetchers = [
        VastAIFetcher(cfg.vast_ai_api_key),
        RunPodFetcher(cfg.runpod_api_key),
        LambdaLabsFetcher(cfg.lambda_labs_api_key),
    ]
    hardware_fetcher = HardwarePriceFetcher()

    logger.info("Fetching GPU prices for: %s", cfg.tracked_gpus)

    rental_tasks = [f.fetch(cfg.tracked_gpus) for f in rental_fetchers]
    hw_task = hardware_fetcher.fetch(cfg.tracked_gpus)

    rental_results_nested, hw_quotes = await asyncio.gather(
        asyncio.gather(*rental_tasks, return_exceptions=True),
        hw_task,
        return_exceptions=True,
    )

    rental_quotes = []
    if isinstance(rental_results_nested, list):
        for r in rental_results_nested:
            if isinstance(r, Exception):
                logger.warning("Rental fetcher error: %s", r)
            else:
                rental_quotes.extend(r)

    if isinstance(hw_quotes, Exception):
        logger.warning("Hardware fetcher error: %s", hw_quotes)
        hw_quotes = []

    logger.info(
        "Collected %d rental quotes and %d hardware quotes",
        len(rental_quotes),
        len(hw_quotes),
    )

    aggregated = aggregate_all(rental_quotes, hw_quotes, cfg.tracked_gpus)

    for p in aggregated:
        logger.info(
            "%-24s  hw=$%.2f  rental=$%.4f/hr  (hw_src=%d rent_src=%d)",
            p.gpu_name,
            p.hardware_price_usd or 0,
            p.rental_price_usd_per_hour or 0,
            p.hardware_sources,
            p.rental_sources,
        )

    tx_hash = chain.submit_batch(aggregated)
    if tx_hash:
        logger.info("On-chain update complete: %s", tx_hash)
    else:
        logger.info("No on-chain update needed")


async def run_loop(cfg: Config) -> None:
    chain = ChainClient(cfg)
    while True:
        start = time.monotonic()
        try:
            await run_once(cfg, chain)
        except Exception as exc:
            logger.error("Oracle round failed: %s", exc, exc_info=True)
        elapsed = time.monotonic() - start
        sleep = max(0, cfg.update_interval_seconds - elapsed)
        logger.info("Sleeping %.0f seconds until next round", sleep)
        await asyncio.sleep(sleep)


def main() -> None:
    cfg = load_config()
    asyncio.run(run_loop(cfg))


if __name__ == "__main__":
    main()
