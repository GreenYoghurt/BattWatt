import pytest
import numpy as np
import pandas as pd
from pathlib import Path
from data_loader import load_meter_data_HomeWizzard, load_price_data, merge_data
from energy_providers import get_providers
from battery import get_battery
from controllers.controller_MPC import Controller_MPC
from simulator import Simulator
from billing import BillingEngine
from models import SimulationResult

# Paths to the real data
PATH_PRICE = Path("../example/day_ahead_2025.xlsx")
PATH_DATA = Path("../example/P1e-2025-1-01-2026-1-01.csv")

@pytest.mark.skipif(not PATH_PRICE.exists() or not PATH_DATA.exists(), 
                    reason="Real data files not found in ../example/")
def test_mpc_e2e_simulation():
    """
    End-to-End test for the MPC controller.
    Compares output values against baseline values and ensures conservation of energy.
    """
    # 1. LOAD & MERGE
    price_df = load_price_data(PATH_PRICE)
    meter_df = load_meter_data_HomeWizzard(PATH_DATA)
    merged_df = merge_data(meter_df, price_df, tolerance="15min")
    merged_df['day_ahead_price'] = merged_df['day_ahead_price']/1000 
    merged_df.set_index("timestamp", drop=False, inplace=True)

    # 2. SETUP BATTERY & CONTROLLER
    # Using the same configuration as example_mpc.py
    battery = get_battery("Bliq_10kwh_fast")
    provider = get_providers()["Zonneplan"]
    # Zonneplan default is net_metering=False in get_providers()
    
    # horizon_hours=24, reoptimize_every_hours=12.0 as in example_mpc.py
    controller = Controller_MPC(battery, merged_df, provider, horizon_hours=24.0, reoptimize_every_hours=12.0)

    # 3. RUN SIMULATION
    simulator = Simulator(battery, controller)
    result = simulator.run(merged_df)

    # 4. ENERGY CONSERVATION ASSERTION
    # (Total Prod - Total Cons) - (Adj Prod - Adj Cons) - Delta SoC = Losses
    # Energy cannot be created, so Losses >= -1e-6
    losses = (result.total_production_kwh - result.total_consumption_kwh) - \
             (result.total_adjusted_production_kwh - result.total_adjusted_consumption_kwh) - \
             result.delta_soc_kwh
    
    assert losses >= -1e-6, f"Energy conservation violated! Losses: {losses:.6f}"

    # 5. FINANCIALS (To compare against baseline)
    billing = BillingEngine(provider)
    
    # Create baseline result (No battery)
    baseline_result = SimulationResult(
        df=merged_df,
        total_production_kwh=merged_df['teruglevering'].sum(),
        total_consumption_kwh=merged_df['verbruik'].sum(),
        total_adjusted_production_kwh=merged_df['teruglevering'].sum(),
        total_adjusted_consumption_kwh=merged_df['verbruik'].sum(),
        final_soc_pct=0,
        final_soc_kwh=0,
        delta_soc_kwh=0
    )

    cost_no_battery = billing.calculate_bill(baseline_result) - provider.get_fixed_costs()
    cost_with_mpc = billing.calculate_bill(result) - provider.get_fixed_costs()
    savings = cost_no_battery - cost_with_mpc

    # Financial Regression (Hardcoded based on Zonneplan with Bliq_10kwh_fast)
    expected_cost_no_battery = 443.28
    expected_cost_with_mpc = 96.78
    
    assert abs(cost_no_battery - expected_cost_no_battery) < 0.05
    assert abs(cost_with_mpc - expected_cost_with_mpc) < 0.05

    # We will use the regression CSV check as well
    baseline_path = Path("tests/mpc_simulation_baseline.csv")
    if baseline_path.exists():
        baseline_df = pd.read_csv(baseline_path)
        baseline_df['timestamp'] = pd.to_datetime(baseline_df['timestamp'])
        
        cols_to_compare = ['adj_prod', 'adj_cons', 'battery_soc']
        mapped_df = result.df.rename(columns={'adjusted_production': 'adj_prod', 'adjusted_consumption': 'adj_cons'})
        
        pd.testing.assert_frame_equal(
            mapped_df[cols_to_compare].reset_index(drop=True),
            baseline_df[cols_to_compare].reset_index(drop=True),
            atol=1e-5
        )
    else:
        # If no baseline CSV exists, we might want to save it if requested
        # result.df[['adjusted_production', 'adjusted_consumption', 'battery_soc']].rename(
        #     columns={'adjusted_production': 'adj_prod', 'adjusted_consumption': 'adj_cons'}
        # ).to_csv(baseline_path, index=False)
        pytest.fail(f"Baseline CSV missing at {baseline_path}. Run simulation to generate it.")

if __name__ == "__main__":
    # Allow running the script directly to see results
    test_mpc_e2e_simulation()
