import pytest
from gpu_price_oracle.aggregator import aggregate_all, aggregate_hardware, aggregate_rental
from gpu_price_oracle.fetchers.base import GPUHardwareQuote, GPURentalQuote


def _rental(name, price, src="test"):
    return GPURentalQuote(gpu_name=name, price_usd_per_hour=price, source=src)


def _hw(name, price, src="test"):
    return GPUHardwareQuote(gpu_name=name, price_usd=price, source=src)


def test_aggregate_rental_single():
    quotes = [_rental("A", 1.0)]
    price, n = aggregate_rental(quotes, "A")
    assert price == pytest.approx(1.0)
    assert n == 1


def test_aggregate_rental_multiple():
    quotes = [_rental("A", 1.0), _rental("A", 1.2), _rental("A", 1.1)]
    price, n = aggregate_rental(quotes, "A")
    assert 1.0 <= price <= 1.2
    assert n == 3


def test_aggregate_rental_outlier_removed():
    # One extreme outlier should be filtered
    quotes = [_rental("A", 1.0), _rental("A", 1.05), _rental("A", 1.1), _rental("A", 50.0)]
    price, n = aggregate_rental(quotes, "A")
    assert price < 5.0, "Outlier should be dropped"


def test_aggregate_hardware_missing():
    price, n = aggregate_hardware([], "A")
    assert price is None
    assert n == 0


def test_aggregate_all_skips_empty():
    results = aggregate_all([], [], ["GPU X"])
    assert results == []


def test_aggregate_all_full():
    rentals = [_rental("RTX 4090", 0.5), _rental("RTX 4090", 0.6)]
    hardware = [_hw("RTX 4090", 1500.0)]
    results = aggregate_all(rentals, hardware, ["RTX 4090"])
    assert len(results) == 1
    r = results[0]
    assert r.gpu_name == "RTX 4090"
    assert r.hardware_price_usd == pytest.approx(1500.0)
    assert 0.5 <= r.rental_price_usd_per_hour <= 0.6
    assert r.hardware_sources == 1
    assert r.rental_sources == 2


def test_aggregate_all_partial_data():
    rentals = [_rental("A100", 2.0)]
    results = aggregate_all(rentals, [], ["A100"])
    assert len(results) == 1
    assert results[0].hardware_price_usd is None
    assert results[0].rental_price_usd_per_hour == pytest.approx(2.0)
