"""
Tests for billing.compare_providers(): billing the same baseline and battery
simulations under several energy contracts (the app's provider comparison).
"""
import numpy as np
import pandas as pd
import pytest

from battery import Battery
from billing import BillingEngine, compare_providers, fixed_costs_from_breakdown
from controllers.controller_PV import Controller_PV
from energy_providers import Provider
from models import SimulationResult
from simulator import Simulator


@pytest.fixture(scope="module")
def merged_df():
    """Two days of synthetic meter + price data (one weekday, one weekend day)."""
    n = 2 * 96
    rng = pd.date_range("2025-06-06", periods=n, freq="15min")  # Fri + Sat
    phase = np.linspace(0, 4 * np.pi, n)
    solar = np.maximum(0, np.sin(phase - np.pi / 2)) * 1.2
    consumption = 0.3 + 0.2 * np.maximum(0, np.cos(phase))
    prices = 0.08 + 0.05 * np.sin(phase)
    df = pd.DataFrame({
        "timestamp": rng,
        "teruglevering": solar,
        "verbruik": consumption,
        "day_ahead_price": prices,
    })
    df = df.set_index("timestamp", drop=False)
    return df


def _baseline(merged_df):
    df = merged_df.copy()
    net = df["teruglevering"] - df["verbruik"]
    df["adjusted_consumption"] = (-net).clip(lower=0)
    df["adjusted_production"] = net.clip(lower=0)
    return SimulationResult(
        df=df,
        total_production_kwh=merged_df["teruglevering"].sum(),
        total_consumption_kwh=merged_df["verbruik"].sum(),
        total_adjusted_production_kwh=df["adjusted_production"].sum(),
        total_adjusted_consumption_kwh=df["adjusted_consumption"].sum(),
        final_soc_pct=0, final_soc_kwh=0, delta_soc_kwh=0,
    )


@pytest.fixture(scope="module")
def simulations(merged_df):
    baseline = _baseline(merged_df)
    results = []
    for cap in (5.0, 10.0):
        battery = Battery(capacity_kwh=cap, max_charge_kw=3.0, max_discharge_kw=3.0, efficiency=0.9)
        results.append(Simulator(battery, Controller_PV(battery)).run(merged_df))
    return baseline, results


def _dynamic(name="Dyn", sub=75.0, buy=0.02, sell=0.02):
    return Provider(name, subscription_cost=sub, buying_fee=buy, selling_fee=sell,
                    net_metering=False, selling_fee_net_metering=True, contract_type="dynamic")


def _fixed(name="Vast", sub=60.0):
    return Provider(name, subscription_cost=sub, buying_fee=0.0, selling_fee=0.0,
                    net_metering=False, selling_fee_net_metering=False, contract_type="fixed",
                    normaal_tarief=0.28, dal_tarief=0.26, terugleververgoeding=0.09)


def test_single_provider_matches_billing_engine(simulations):
    baseline, results = simulations
    provider = _dynamic()
    comparison = compare_providers([provider], baseline, results)
    engine = BillingEngine(provider)

    assert len(comparison) == 1
    entry = comparison[0]
    assert entry["name"] == "Dyn"
    assert entry["contract_type"] == "dynamic"
    assert entry["cost_baseline"] == pytest.approx(engine.calculate_bill(baseline))
    assert len(entry["results"]) == len(results)
    for row, result in zip(entry["results"], results):
        expected = engine.calculate_bill(result)
        assert row["cost_total"] == pytest.approx(expected)
        assert row["savings"] == pytest.approx(entry["cost_baseline"] - expected)
        assert row["breakdown"] == engine.calculate_bill_breakdown(result)


def test_order_and_contract_types_preserved(simulations):
    baseline, results = simulations
    providers = [_fixed("A"), _dynamic("B"), _dynamic("C", buy=0.05)]
    comparison = compare_providers(providers, baseline, results)
    assert [c["name"] for c in comparison] == ["A", "B", "C"]
    assert [c["contract_type"] for c in comparison] == ["fixed", "dynamic", "dynamic"]
    for c, p in zip(comparison, providers):
        assert c["cost_baseline"] == pytest.approx(BillingEngine(p).calculate_bill(baseline))


def test_variable_cost_excludes_fixed_costs_and_savings_are_unaffected(simulations):
    baseline, results = simulations
    entry = compare_providers([_fixed()], baseline, results)[0]
    fixed = fixed_costs_from_breakdown(entry["breakdown_baseline"])
    assert entry["cost_baseline_variable"] == pytest.approx(entry["cost_baseline"] - fixed)
    for row in entry["results"]:
        assert row["cost_variable"] == pytest.approx(row["cost_total"] - fixed)
        # Fixed costs cancel out of the savings.
        assert row["savings"] == pytest.approx(entry["cost_baseline_variable"] - row["cost_variable"])


def test_subscription_only_shifts_cost_not_savings(simulations):
    baseline, results = simulations
    cheap, pricey = compare_providers([_dynamic("cheap", sub=50.0), _dynamic("pricey", sub=150.0)],
                                      baseline, results)
    assert pricey["cost_baseline"] - cheap["cost_baseline"] == pytest.approx(100.0)
    for a, b in zip(cheap["results"], pricey["results"]):
        assert b["cost_total"] - a["cost_total"] == pytest.approx(100.0)
        assert b["savings"] == pytest.approx(a["savings"])


def test_higher_buying_fee_raises_cost_and_battery_savings(simulations):
    """A higher purchase mark-up makes every imported kWh dearer, so the PV
    battery (which cuts imports) saves more under that contract."""
    baseline, results = simulations
    low, high = compare_providers([_dynamic("low", buy=0.01), _dynamic("high", buy=0.10)],
                                  baseline, results)
    assert high["cost_baseline"] > low["cost_baseline"]
    for a, b in zip(low["results"], high["results"]):
        assert b["savings"] > a["savings"]


def test_does_not_mutate_simulation_dataframes(simulations):
    baseline, results = simulations
    before = [baseline.df.copy()] + [r.df.copy() for r in results]
    compare_providers([_fixed(), _dynamic()], baseline, results)
    for snapshot, res in zip(before, [baseline] + results):
        pd.testing.assert_frame_equal(snapshot, res.df)


def test_no_providers_or_no_results(simulations):
    baseline, results = simulations
    assert compare_providers([], baseline, results) == []
    entry = compare_providers([_dynamic()], baseline, [])[0]
    assert entry["results"] == []
