"""
Tests for the 'vast contract' (fixed contract) pricing model:
- get_tariff_period() normaal/dal window boundaries
- Provider validation and Provider.calculate_fixed_costs_breakdown()
- BillingEngine end-to-end with a fixed-contract Provider
"""
import datetime

import numpy as np
import pandas as pd
import pytest

from energy_providers import Provider, get_tariff_period, get_grid_operator_fee, get_tax_discount
from billing import BillingEngine
from battery import Battery
from controllers.controller_PV import Controller_PV
from simulator import Simulator
from models import SimulationResult


# ── get_tariff_period ───────────────────────────────────────────────────────

@pytest.mark.parametrize("dt, expected", [
    (datetime.datetime(2025, 6, 2, 6, 59), "dal"),      # Monday 06:59
    (datetime.datetime(2025, 6, 2, 7, 0), "normaal"),    # Monday 07:00
    (datetime.datetime(2025, 6, 2, 22, 59), "normaal"),  # Monday 22:59
    (datetime.datetime(2025, 6, 2, 23, 0), "dal"),       # Monday 23:00
    (datetime.datetime(2025, 6, 7, 12, 0), "dal"),       # Saturday noon
    (datetime.datetime(2025, 6, 8, 12, 0), "dal"),       # Sunday noon
])
def test_get_tariff_period(dt, expected):
    assert get_tariff_period(dt) == expected


def test_get_tariff_period_accepts_pandas_timestamp():
    assert get_tariff_period(pd.Timestamp("2025-06-02 08:00")) == "normaal"
    assert get_tariff_period(pd.Timestamp("2025-06-02 23:30")) == "dal"


# ── Provider validation ─────────────────────────────────────────────────────

def test_provider_fixed_requires_tariffs():
    with pytest.raises(ValueError):
        Provider(
            "Test", subscription_cost=0.0, buying_fee=0.0, selling_fee=0.0,
            net_metering=False, selling_fee_net_metering=False,
            contract_type="fixed",
        )


def test_provider_rejects_unknown_contract_type():
    with pytest.raises(ValueError):
        Provider(
            "Test", subscription_cost=0.0, buying_fee=0.0, selling_fee=0.0,
            net_metering=False, selling_fee_net_metering=False,
            contract_type="bogus",
        )


# ── Provider.calculate_fixed_costs_breakdown ────────────────────────────────

def _fixed_provider(subscription_cost=0.0):
    return Provider(
        "Test", subscription_cost=subscription_cost, buying_fee=0.0, selling_fee=0.0,
        net_metering=False, selling_fee_net_metering=False,
        contract_type="fixed", normaal_tarief=0.30, dal_tarief=0.20, terugleververgoeding=0.10,
    )


def test_fixed_costs_breakdown_splits_normaal_and_dal():
    provider = _fixed_provider()
    datetime_index = [
        datetime.datetime(2025, 6, 2, 8, 0),   # Monday normaal
        datetime.datetime(2025, 6, 2, 23, 30),  # Monday dal
        datetime.datetime(2025, 6, 7, 12, 0),   # Saturday dal
    ]
    consumption_kwh = [2.0, 1.0, 3.0]
    feed_in_kwh = [0.5, 0.5, 1.0]

    bd = provider.calculate_fixed_costs_breakdown(consumption_kwh, feed_in_kwh, datetime_index)

    assert bd["normaal_kwh"] == pytest.approx(2.0)
    assert bd["dal_kwh"] == pytest.approx(4.0)
    assert bd["normaal_kosten"] == pytest.approx(0.6)
    assert bd["dal_kosten"] == pytest.approx(0.8)
    assert bd["teruglevering_opbrengst"] == pytest.approx(0.2)
    assert bd["total_flexible"] == pytest.approx(1.2)
    assert bd["total_consumption_kwh"] == pytest.approx(6.0)
    assert bd["total_feed_in_kwh"] == pytest.approx(2.0)


def test_calculate_costs_total_dispatches_to_fixed():
    provider = _fixed_provider()
    datetime_index = [datetime.datetime(2025, 6, 2, 8, 0)]
    total = provider.calculate_costs_total(
        consumption_kwh=[2.0], feed_in_kwh=[0.0], datetime_index=datetime_index,
    )
    assert total == pytest.approx(0.6)


# ── BillingEngine end-to-end ─────────────────────────────────────────────────

def test_billing_engine_fixed_contract_breakdown():
    provider = _fixed_provider(subscription_cost=50.0)
    idx = pd.DatetimeIndex([
        pd.Timestamp("2025-06-02 08:00"),   # normaal
        pd.Timestamp("2025-06-02 23:30"),   # dal
        pd.Timestamp("2025-06-07 12:00"),   # dal (Saturday)
    ])
    df = pd.DataFrame({
        "verbruik": [2.0, 1.0, 3.0],
        "teruglevering": [0.5, 0.5, 1.0],
        "day_ahead_price": [0.05, 0.05, 0.05],
    }, index=idx)

    result = SimulationResult(
        df=df,
        total_production_kwh=2.0,
        total_consumption_kwh=6.0,
        total_adjusted_production_kwh=2.0,
        total_adjusted_consumption_kwh=6.0,
        final_soc_pct=0,
        final_soc_kwh=0,
        delta_soc_kwh=0,
    )

    billing = BillingEngine(provider)
    bd = billing.calculate_bill_breakdown(result)

    expected_flexible = 1.2  # from test_fixed_costs_breakdown_splits_normaal_and_dal
    expected_fixed = 50.0 + get_grid_operator_fee("Enexis") - get_tax_discount(2025)
    assert bd["contract_type"] == "fixed"
    assert bd["total"] == pytest.approx(expected_fixed + expected_flexible)
    assert bd["total"] == pytest.approx(billing.calculate_bill(result))

    # Dynamic-only keys must not leak into a fixed-contract breakdown.
    assert "marktprijs_inkoop" not in bd
    assert "energiebelasting" not in bd


# ── Full pipeline E2E: Controller_PV + Simulator + fixed-contract BillingEngine ─

def _fixed_contract_provider():
    return Provider(
        "Test", subscription_cost=75.0, buying_fee=0.0, selling_fee=0.0,
        net_metering=False, selling_fee_net_metering=False,
        contract_type="fixed", normaal_tarief=0.30, dal_tarief=0.20, terugleververgoeding=0.10,
    )


def _classify_dal(rng: pd.DatetimeIndex) -> np.ndarray:
    """Independent re-derivation of the normaal/dal spec (not calling
    get_tariff_period), used to hand-verify the baseline breakdown."""
    is_weekend = rng.dayofweek >= 5
    is_night = (rng.hour >= 23) | (rng.hour < 7)
    return is_weekend | (~is_weekend & is_night)


@pytest.fixture(scope="module")
def merged_df_two_days():
    """48h of synthetic meter data spanning one weekday and one weekend day,
    so both normaal and dal windows are exercised."""
    n_per_day = 96  # 15-min intervals
    day_shape = np.maximum(0, np.sin(np.linspace(-np.pi / 4, 5 * np.pi / 4, n_per_day))) * 1.5
    solar = np.tile(day_shape, 2)
    consumption = np.full(n_per_day * 2, 0.4)
    prices = np.tile(0.05 + 0.03 * np.sin(np.linspace(0, 2 * np.pi, n_per_day)), 2)

    # Any Monday works; day 2 lands on Tuesday which is still a weekday, so
    # start on a Friday instead to get one weekday + one full weekend day.
    rng = pd.date_range("2025-06-06", periods=n_per_day * 2, freq="15min")  # Fri 00:00 -> Sun 00:00
    assert rng[0].dayofweek == 4  # Friday
    assert rng[n_per_day].dayofweek == 5  # Saturday

    df = pd.DataFrame({
        "timestamp": rng,
        "teruglevering": solar,
        "verbruik": consumption,
        "day_ahead_price": prices,
    })
    df.set_index("timestamp", drop=False, inplace=True)
    return df


def test_fixed_contract_pv_controller_e2e(merged_df_two_days):
    """
    E2E test for Controller_PV + Simulator running against a fixed-contract
    Provider, mirroring tests/test_pv_controller.py's pattern for the dynamic
    contract path.
    """
    merged_df = merged_df_two_days
    provider = _fixed_contract_provider()
    billing = BillingEngine(provider)

    # 1. BASELINE (no battery) — independently hand-derived so the fixed-
    #    contract billing math is verified end-to-end, not just re-asserted
    #    against Provider.calculate_fixed_costs_breakdown() itself.
    baseline_df = merged_df.copy()
    net = baseline_df["teruglevering"] - baseline_df["verbruik"]
    baseline_df["adjusted_consumption"] = (-net).clip(lower=0)
    baseline_df["adjusted_production"] = net.clip(lower=0)

    baseline_result = SimulationResult(
        df=baseline_df,
        total_production_kwh=merged_df["teruglevering"].sum(),
        total_consumption_kwh=merged_df["verbruik"].sum(),
        total_adjusted_production_kwh=baseline_df["adjusted_production"].sum(),
        total_adjusted_consumption_kwh=baseline_df["adjusted_consumption"].sum(),
        final_soc_pct=0,
        final_soc_kwh=0,
        delta_soc_kwh=0,
    )

    dal_mask = _classify_dal(merged_df.index)
    expected_normaal_kwh = baseline_df["adjusted_consumption"][~dal_mask].sum()
    expected_dal_kwh = baseline_df["adjusted_consumption"][dal_mask].sum()
    expected_feed_in = baseline_df["adjusted_production"].sum()

    expected_normaal_kosten = expected_normaal_kwh * provider.normaal_tarief
    expected_dal_kosten = expected_dal_kwh * provider.dal_tarief
    expected_teruglevering_opbrengst = expected_feed_in * provider.terugleververgoeding
    expected_fixed_costs = provider.subscription_cost + get_grid_operator_fee("Enexis") - get_tax_discount(2025)
    expected_baseline_total = (
        expected_fixed_costs + expected_normaal_kosten + expected_dal_kosten - expected_teruglevering_opbrengst
    )

    breakdown_baseline = billing.calculate_bill_breakdown(baseline_result)
    assert breakdown_baseline["normaal_kwh"] == pytest.approx(expected_normaal_kwh)
    assert breakdown_baseline["dal_kwh"] == pytest.approx(expected_dal_kwh)
    assert breakdown_baseline["normaal_kosten"] == pytest.approx(expected_normaal_kosten)
    assert breakdown_baseline["dal_kosten"] == pytest.approx(expected_dal_kosten)
    assert breakdown_baseline["teruglevering_opbrengst"] == pytest.approx(expected_teruglevering_opbrengst)
    assert breakdown_baseline["total"] == pytest.approx(expected_baseline_total)
    cost_baseline = billing.calculate_bill(baseline_result)
    assert cost_baseline == pytest.approx(expected_baseline_total)

    # 2. SIMULATED (with battery) — run the real Controller_PV + Simulator
    #    pipeline against the fixed-contract provider.
    bat = Battery(capacity_kwh=5.0, max_charge_kw=3.0, max_discharge_kw=3.0, efficiency=0.9, standby_power_w=0.0)
    controller = Controller_PV(bat)
    simulator = Simulator(bat, controller)
    result = simulator.run(merged_df)

    breakdown_simulated = billing.calculate_bill_breakdown(result)
    cost_simulated = billing.calculate_bill(result)

    # Structural checks: the breakdown must stay internally consistent
    # whatever the battery decided to do.
    assert breakdown_simulated["normaal_kwh"] + breakdown_simulated["dal_kwh"] == pytest.approx(
        result.total_adjusted_consumption_kwh
    )
    assert breakdown_simulated["total"] == pytest.approx(cost_simulated)

    # Energy conservation, same invariant as the other e2e tests: losses
    # (production - consumption, netted against grid flow and battery SoC
    # change) must be non-negative — energy cannot be created.
    assert (result.total_production_kwh - result.total_consumption_kwh) - \
           (result.total_adjusted_production_kwh - result.total_adjusted_consumption_kwh) - \
           result.delta_soc_kwh >= -1e-6

    # The battery shifts self-consumption away from buying at normaal/dal
    # tariff, which is worth strictly more than the terugleververgoeding it
    # would otherwise have earned exporting that energy — so it must save
    # money under a fixed contract too.
    savings = cost_baseline - cost_simulated
    assert savings > 0

    # Regression baseline, pinned from an actual run of the code above.
    # Negative because the full-year fixed costs (netbeheerskosten,
    # belastingvermindering) aren't prorated to this 2-day dataset — see
    # docs/cost-calculation.md's note on partial-year data.
    expected_simulated_cost = -86.35
    assert abs(cost_simulated - expected_simulated_cost) < 0.05
