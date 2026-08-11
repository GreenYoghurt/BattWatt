"""
Tests for the sequential multi-battery comparison simulation pipeline.

Covers behaviors specific to running multiple battery configs in sequence:
- Independence (no state leaks between runs)
- DataFrame immutability (source data not mutated)
- Savings ordering (larger battery >= smaller battery for PV strategy)
- Baseline stability (baseline cost unchanged after subsequent battery runs)
- Energy conservation per battery
- Breakdown key completeness (required by _render_unified_breakdown in app.py)
- Breakdown total consistency with calculate_bill()
"""
import pytest
import numpy as np
import pandas as pd
from battery import Battery
from controllers.controller_PV import Controller_PV
from simulator import Simulator
from billing import BillingEngine
from energy_providers import Provider
from models import SimulationResult


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def merged_df():
    """24 h of synthetic meter + price data with a full DatetimeIndex."""
    n = 96  # 15-min intervals
    rng = pd.date_range("2025-06-01", periods=n, freq="15min")
    solar = np.maximum(0, np.sin(np.linspace(-np.pi / 4, 5 * np.pi / 4, n))) * 1.5
    consumption = np.full(n, 0.4)
    prices = 0.05 + 0.03 * np.sin(np.linspace(0, 2 * np.pi, n))

    df = pd.DataFrame({
        "timestamp": rng,
        "teruglevering": solar,
        "verbruik": consumption,
        "day_ahead_price": prices,
    })
    df.set_index("timestamp", drop=False, inplace=True)
    return df


@pytest.fixture(scope="module")
def provider():
    return Provider(
        "Test", subscription_cost=0.0, buying_fee=0.02, selling_fee=0.02,
        net_metering=False, selling_fee_net_metering=False,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_baseline(merged_df: pd.DataFrame) -> SimulationResult:
    """Replicates the baseline computation from app._run_simulation."""
    baseline_df = merged_df.copy()
    net = baseline_df["teruglevering"] - baseline_df["verbruik"]
    baseline_df["adjusted_consumption"] = (-net).clip(lower=0)
    baseline_df["adjusted_production"] = net.clip(lower=0)
    return SimulationResult(
        df=baseline_df,
        total_production_kwh=merged_df["teruglevering"].sum(),
        total_consumption_kwh=merged_df["verbruik"].sum(),
        total_adjusted_production_kwh=baseline_df["adjusted_production"].sum(),
        total_adjusted_consumption_kwh=baseline_df["adjusted_consumption"].sum(),
        final_soc_pct=0,
        final_soc_kwh=0,
        delta_soc_kwh=0,
    )


def _run_pv(merged_df: pd.DataFrame, battery: Battery, provider: Provider):
    """Run one PV simulation; returns (SimulationResult, total_bill)."""
    billing = BillingEngine(provider)
    controller = Controller_PV(battery)
    simulator = Simulator(battery, controller)
    result = simulator.run(merged_df)
    result.df["battery_soc_kwh"] = result.df["battery_soc"] * battery.capacity_kwh / 100
    return result, billing.calculate_bill(result)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_sequential_runs_are_order_independent(merged_df, provider):
    """Running battery A then B must give the same costs as running B then A."""
    def fresh_batteries():
        return (
            Battery(5.0, max_charge_kw=3.0, max_discharge_kw=3.0,
                    efficiency=1.0, standby_power_w=0.0),
            Battery(10.0, max_charge_kw=3.0, max_discharge_kw=3.0,
                    efficiency=1.0, standby_power_w=0.0),
        )

    bat_a1, bat_b1 = fresh_batteries()
    _, cost_a_first = _run_pv(merged_df, bat_a1, provider)
    _, cost_b_second = _run_pv(merged_df, bat_b1, provider)

    bat_a2, bat_b2 = fresh_batteries()
    _, cost_b_first = _run_pv(merged_df, bat_b2, provider)
    _, cost_a_second = _run_pv(merged_df, bat_a2, provider)

    assert abs(cost_a_first - cost_a_second) < 1e-9, "Battery A cost differs by run order"
    assert abs(cost_b_first - cost_b_second) < 1e-9, "Battery B cost differs by run order"


def test_source_dataframe_not_mutated(merged_df, provider):
    """simulator.run() must not modify the shared merged_df."""
    verbruik_before = merged_df["verbruik"].copy()
    teruglevering_before = merged_df["teruglevering"].copy()

    bat = Battery(5.0, max_charge_kw=3.0, max_discharge_kw=3.0,
                  efficiency=1.0, standby_power_w=0.0)
    _run_pv(merged_df, bat, provider)

    pd.testing.assert_series_equal(merged_df["verbruik"], verbruik_before)
    pd.testing.assert_series_equal(merged_df["teruglevering"], teruglevering_before)
    assert "adjusted_consumption" not in merged_df.columns, \
        "simulator.run() must not add columns to the source DataFrame"


def test_larger_battery_saves_at_least_as_much(merged_df, provider):
    """Increasing battery capacity must not decrease savings under PV strategy."""
    billing = BillingEngine(provider)
    cost_baseline = billing.calculate_bill(_build_baseline(merged_df))

    capacities = [2.0, 5.0, 10.0]
    savings = []
    for cap in capacities:
        bat = Battery(cap, max_charge_kw=3.0, max_discharge_kw=3.0,
                      efficiency=1.0, standby_power_w=0.0)
        _, cost = _run_pv(merged_df, bat, provider)
        savings.append(cost_baseline - cost)

    for i in range(len(savings) - 1):
        assert savings[i + 1] >= savings[i] - 1e-9, (
            f"{capacities[i + 1]} kWh battery savings ({savings[i + 1]:.4f}) "
            f"< {capacities[i]} kWh savings ({savings[i]:.4f})"
        )


def test_baseline_unchanged_after_battery_runs(merged_df, provider):
    """The baseline cost must be identical regardless of how many battery sims follow."""
    billing = BillingEngine(provider)
    baseline = _build_baseline(merged_df)
    cost_before = billing.calculate_bill(baseline)

    for cap in [2.0, 5.0, 10.0]:
        bat = Battery(cap, max_charge_kw=3.0, max_discharge_kw=3.0,
                      efficiency=1.0, standby_power_w=0.0)
        _run_pv(merged_df, bat, provider)

    cost_after = billing.calculate_bill(baseline)
    assert cost_before == cost_after, "Baseline cost changed after running battery simulations"


def test_pv_battery_always_reduces_cost(merged_df, provider):
    """PV strategy with surplus solar must never increase cost versus baseline."""
    billing = BillingEngine(provider)
    cost_baseline = billing.calculate_bill(_build_baseline(merged_df))

    for cap in [2.0, 5.0, 10.0, 15.0]:
        bat = Battery(cap, max_charge_kw=3.68, max_discharge_kw=3.68,
                      efficiency=0.9604, standby_power_w=0.0)
        _, cost = _run_pv(merged_df, bat, provider)
        assert cost <= cost_baseline + 1e-6, (
            f"{cap} kWh battery increased cost: {cost:.4f} > baseline {cost_baseline:.4f}"
        )


def test_energy_conservation_per_battery(merged_df, provider):
    """Energy conservation must hold independently for each battery simulation."""
    configs = [
        Battery(5.0, max_charge_kw=3.0, max_discharge_kw=3.0,
                efficiency=0.9025, standby_power_w=0.0),
        Battery(10.0, max_charge_kw=5.0, max_discharge_kw=5.0,
                efficiency=0.9604, standby_power_w=0.0),
    ]
    for bat in configs:
        result, _ = _run_pv(merged_df, bat, provider)
        losses = (
            (result.total_production_kwh - result.total_consumption_kwh)
            - (result.total_adjusted_production_kwh - result.total_adjusted_consumption_kwh)
            - result.delta_soc_kwh
        )
        assert losses >= -1e-6, (
            f"Energy conservation violated for {bat.capacity_kwh} kWh battery: {losses:.8f}"
        )


def test_breakdown_contains_all_required_keys(merged_df, provider):
    """calculate_bill_breakdown must return every key accessed by _render_unified_breakdown."""
    required_keys = {
        "abonnementskosten", "netbeheerskosten", "belastingvermindering",
        "marktprijs_inkoop", "energiebelasting", "leveranciersopslag_inkoop",
        "leveranciersopslag_verkoop", "teruglevering_opbrengst",
        "total_consumption_kwh", "total_feed_in_kwh", "energiebelasting_kwh",
        "tarief_abonnementskosten", "tarief_netbeheerskosten", "tarief_belastingvermindering",
        "tarief_energiebelasting_per_kwh",
        "tarief_leveranciersopslag_inkoop_per_kwh",
        "tarief_leveranciersopslag_verkoop_per_kwh",
    }
    billing = BillingEngine(provider)

    for result_obj in [
        _build_baseline(merged_df),
        _run_pv(
            merged_df,
            Battery(5.0, max_charge_kw=3.0, max_discharge_kw=3.0,
                    efficiency=1.0, standby_power_w=0.0),
            provider,
        )[0],
    ]:
        breakdown = billing.calculate_bill_breakdown(result_obj)
        missing = required_keys - set(breakdown.keys())
        assert not missing, f"Breakdown missing keys: {missing}"


def test_breakdown_total_matches_calculate_bill(merged_df, provider):
    """breakdown['total'] must equal calculate_bill() to floating-point precision."""
    billing = BillingEngine(provider)
    bat = Battery(5.0, max_charge_kw=3.0, max_discharge_kw=3.0,
                  efficiency=1.0, standby_power_w=0.0)
    result, bill = _run_pv(merged_df, bat, provider)

    breakdown = billing.calculate_bill_breakdown(result)
    assert abs(breakdown["total"] - bill) < 1e-6, (
        f"breakdown['total']={breakdown['total']:.6f} != calculate_bill()={bill:.6f}"
    )
