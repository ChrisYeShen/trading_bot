"""Unit tests for chain_client conversion helpers (no web3 connection needed)."""
import pytest
from gpu_price_oracle.chain_client import _usd_to_cents, _usd_per_hour_to_milli_cents


def test_usd_to_cents():
    assert _usd_to_cents(1.0) == 100
    assert _usd_to_cents(1599.0) == 159900
    assert _usd_to_cents(None) == 0
    assert _usd_to_cents(0.0) == 0
    assert _usd_to_cents(-1.0) == 0  # negative clamped


def test_usd_per_hour_to_milli_cents():
    # $1.00/hr = 100,000 milli-cents
    assert _usd_per_hour_to_milli_cents(1.0) == 100_000
    # $0.30/hr = 30,000 milli-cents
    assert _usd_per_hour_to_milli_cents(0.30) == 30_000
    assert _usd_per_hour_to_milli_cents(None) == 0
    assert _usd_per_hour_to_milli_cents(-0.5) == 0
