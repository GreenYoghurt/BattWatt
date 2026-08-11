import pytest
import numpy as np
import pandas as pd
from pathlib import Path
from data_loader import load_meter_data_HomeWizzard, load_price_data, merge_data, SmartLoader
from energy_providers import get_providers
from battery import Battery, get_battery
from controllers.controller_price import Controller_price
from simulator import Simulator
from billing import BillingEngine
from models import SimulationResult

# Paths to the real data (E2E uses the actual dataset)
PATH_PRICE = Path("tests/data/day_ahead_2025.xlsx")
PATH_DATA = Path("tests/data/test_2025.csv")

def _synthetic_price_df(timestamps: pd.Series) -> pd.DataFrame:
    """Deterministic cheap-at-night/expensive-at-day price series (EUR/kWh),
    used for e2e fixtures whose dates fall outside the recorded ENTSO-E export."""
    hours = timestamps.dt.hour + timestamps.dt.minute / 60
    price = 0.20 + 0.10 * np.sin(2 * np.pi * (hours - 6) / 24)
    return pd.DataFrame({"timestamp": timestamps, "day_ahead_price": price})

def _run_pipeline(meter_df: pd.DataFrame):
    """Run the full load->merge->simulate->bill pipeline on an already-loaded
    meter dataframe, mirroring test_battwatt_e2e_simulation."""
    price_df = _synthetic_price_df(meter_df["timestamp"])
    merged_df = merge_data(meter_df, price_df, tolerance="15min")
    merged_df.set_index("timestamp", drop=False, inplace=True)

    # Pin efficiency explicitly so this regression test doesn't drift if the
    # Battery class's default efficiency changes.
    bat = get_battery("Bliq_5kwh", efficiency=0.9604)
    controller = Controller_price(bat, merged_df)
    simulator = Simulator(bat, controller)
    result = simulator.run(merged_df)

    provider = get_providers()["Zonneplan"]
    provider.net_metering = False
    billing = BillingEngine(provider)

    baseline_df = merged_df.copy()
    net = baseline_df['teruglevering'] - baseline_df['verbruik']
    baseline_df['adjusted_consumption'] = (-net).clip(lower=0)
    baseline_df['adjusted_production'] = net.clip(lower=0)

    baseline_result = SimulationResult(
        df=baseline_df,
        total_production_kwh=merged_df['teruglevering'].sum(),
        total_consumption_kwh=merged_df['verbruik'].sum(),
        total_adjusted_production_kwh=baseline_df['adjusted_production'].sum(),
        total_adjusted_consumption_kwh=baseline_df['adjusted_consumption'].sum(),
        final_soc_pct=0,
        final_soc_kwh=0,
        delta_soc_kwh=0
    )

    cost_baseline = billing.calculate_bill(baseline_result) - provider.get_fixed_costs()
    cost_simulated = billing.calculate_bill(result) - provider.get_fixed_costs()
    return result, cost_baseline, cost_simulated

def _assert_energy_conservation(result):
    assert (result.total_production_kwh - result.total_consumption_kwh) - \
           (result.total_adjusted_production_kwh - result.total_adjusted_consumption_kwh) - \
           result.delta_soc_kwh >= -1e-6

def test_battwatt_e2e_simulation():
    """
    End-to-End test for the BattWatt tool using the unified Simulator.
    """
    # 1. LOAD & MERGE
    price_df = load_price_data(PATH_PRICE)
    meter_df = load_meter_data_HomeWizzard(PATH_DATA)
    merged_df = merge_data(meter_df, price_df, tolerance="15min")
    merged_df['day_ahead_price'] = merged_df['day_ahead_price']/1000 
    merged_df.set_index("timestamp", drop=False, inplace=True)

    # 2. RUN SIMULATION
    # Pin efficiency explicitly so this regression test doesn't drift if the
    # Battery class's default efficiency changes.
    bat = get_battery("Bliq_5kwh", efficiency=0.9604)
    controller = Controller_price(bat, merged_df)
    simulator = Simulator(bat, controller)
    result = simulator.run(merged_df)


    # 3. FINANCIALS
    provider = get_providers()["Zonneplan"]
    # Force the provider state for the test (E2E was recorded with net_metering=False for some reason in the baseline? No, actually it was True then False... wait)
    # The baseline values I hardcoded were for net_metering=False.
    provider.net_metering = False 
    
    billing = BillingEngine(provider)
    
    # Baseline: net per-interval flows so billing uses the same logic as simulated result
    baseline_df = merged_df.copy()
    net = baseline_df['teruglevering'] - baseline_df['verbruik']
    baseline_df['adjusted_consumption'] = (-net).clip(lower=0)
    baseline_df['adjusted_production'] = net.clip(lower=0)

    baseline_result = SimulationResult(
        df=baseline_df,
        total_production_kwh=merged_df['teruglevering'].sum(),
        total_consumption_kwh=merged_df['verbruik'].sum(),
        total_adjusted_production_kwh=baseline_df['adjusted_production'].sum(),
        total_adjusted_consumption_kwh=baseline_df['adjusted_consumption'].sum(),
        final_soc_pct=0,
        final_soc_kwh=0,
        delta_soc_kwh=0
    )

    cost_baseline = billing.calculate_bill(baseline_result) - provider.get_fixed_costs() # Flexible only
    cost_simulated = billing.calculate_bill(result) - provider.get_fixed_costs() # Flexible only
    savings = cost_baseline - cost_simulated

    # 4. ASSERTIONS
    
    # Energy Conservation
    assert (result.total_production_kwh - result.total_consumption_kwh) - \
           (result.total_adjusted_production_kwh - result.total_adjusted_consumption_kwh) - \
           result.delta_soc_kwh >= -1e-6

    # Financial Regression (Hardcoded based on net_metering=False)
    # Baseline uses per-interval netted flows (not raw verbruik/teruglevering) so
    # savings reflect only the battery contribution. Was 443.30 before the fix.
    expected_baseline_cost = 430.73
    expected_simulated_cost = 412.55
    expected_cycles = 354.40
    
    assert abs(cost_baseline - expected_baseline_cost) < 0.05
    assert abs(cost_simulated - expected_simulated_cost) < 0.05
    assert abs(result.total_cycles - expected_cycles) < 0.05

    # 5. REGRESSION CHECK (CSV)
    baseline_path = Path("tests/simulation_baseline.csv")
    if baseline_path.exists():
        baseline_df = pd.read_csv(baseline_path)
        baseline_df['timestamp'] = pd.to_datetime(baseline_df['timestamp'])
        
        cols_to_compare = ['adj_prod', 'adj_cons', 'battery_soc']
        # Map our result columns to the expected ones if they differ
        mapped_df = result.df.rename(columns={'adjusted_production': 'adj_prod', 'adjusted_consumption': 'adj_cons'})
        
        pd.testing.assert_frame_equal(
            mapped_df[cols_to_compare].reset_index(drop=True),
            baseline_df[cols_to_compare].reset_index(drop=True),
            atol=1e-5
        )

def test_e2e_slimme_meter_portal_format():
    """E2E coverage for the SlimmeMeterPortal.nl Excel format (Tijdstip +
    normaal/laag tariff columns), routed through SmartLoader auto-detection."""
    meter_df = SmartLoader.load(Path("tests/data/SlimmeMeterPortal.xlsx"))
    result, cost_baseline, cost_simulated = _run_pipeline(meter_df)

    _assert_energy_conservation(result)

    expected_baseline_cost = -0.60
    expected_simulated_cost = 0.49
    assert abs(cost_baseline - expected_baseline_cost) < 0.05
    assert abs(cost_simulated - expected_simulated_cost) < 0.05

def test_e2e_single_column_format():
    """E2E coverage for the Kwartierdata single-column format (signed net
    power, positive = consumption / negative = production)."""
    meter_df = SmartLoader.load(Path("tests/data/Kwartierdata_single_column.xlsx"))
    result, cost_baseline, cost_simulated = _run_pipeline(meter_df)

    _assert_energy_conservation(result)

    expected_baseline_cost = 102.22
    expected_simulated_cost = 92.27
    assert abs(cost_baseline - expected_baseline_cost) < 0.05
    assert abs(cost_simulated - expected_simulated_cost) < 0.05
