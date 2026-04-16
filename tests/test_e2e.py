import pytest
import numpy as np
import pandas as pd
from pathlib import Path
from data_loader import load_meter_data_HomeWizzard, load_price_data, merge_data
from energy_providers import get_providers
from battery import Battery, get_battery
from controller_price import Controller_price

# Paths to the real data (E2E uses the actual dataset)
PATH_PRICE = Path("../example/day_ahead_2025.xlsx")
PATH_DATA = Path("../example/P1e-2025-1-01-2026-1-01.csv")

@pytest.mark.skipif(not PATH_PRICE.exists() or not PATH_DATA.exists(), 
                    reason="Real data files not found in ../example/")
def test_battwatt_e2e_simulation():
    """
    End-to-End test for the BattWatt tool.
    Validates: Data loading -> Merging -> Simulation -> Financials -> Conservation.
    """
    # 1. LOAD & MERGE
    price_df = load_price_data(PATH_PRICE)
    meter_df = load_meter_data_HomeWizzard(PATH_DATA)
    
    # Ensure data is loaded
    assert len(price_df) > 0, "Price data is empty"
    assert len(meter_df) > 0, "Meter data is empty"

    merged_df = merge_data(meter_df, price_df, tolerance="15min")
    merged_df['day_ahead_price'] = merged_df['day_ahead_price']/1000  # MWh -> kWh
    merged_df.set_index("timestamp", drop=False, inplace=True)

    # 2. INITIAL FINANCIALS (Baseline)
    provider_name = "Zonneplan"
    Provider = get_providers()[provider_name]
    
    total_cost_baseline = Provider.calculate_flexible_costs(
        consumption_kwh=merged_df["verbruik"].tolist(),
        feed_in_kwh=merged_df["teruglevering"].tolist(),
        prices_eur_per_kwh_excl_vat=(merged_df["day_ahead_price"]).tolist()
    )

    # 3. RUN SIMULATION
    bat = get_battery("Bliq_5kwh")
    controller = Controller_price(bat, merged_df)
    initial_soc_kwh = bat.get_soc_kwh()
    
    for index, row in merged_df.iterrows():
        prod, cons = row['teruglevering'], row['verbruik']
        
        to_bat, from_bat = controller.step(prod, cons, datetime_index=index, duration_hours=0.25)
        to_grid_bat, from_grid_bat = bat.step(to_bat, from_bat, duration_hours=0.25)
        
        # Grid interaction (Fix: ensure non-negative grid flow)
        net_grid_energy = (prod - cons) - (to_bat - from_bat) + (to_grid_bat - from_grid_bat)
        if net_grid_energy >= 0:
            prod2 = net_grid_energy
            cons2 = 0
        else:
            prod2 = 0
            cons2 = -net_grid_energy

        merged_df.at[index, 'adj_prod'] = prod2
        merged_df.at[index, 'adj_cons'] = cons2
        merged_df.at[index, 'battery_soc'] = bat.get_soc()

    # 4. FINAL FINANCIALS (With Battery)
    total_cost_simulated = Provider.calculate_flexible_costs(
        consumption_kwh=merged_df["adj_cons"].tolist(),
        feed_in_kwh=merged_df["adj_prod"].tolist(),
        prices_eur_per_kwh_excl_vat=(merged_df["day_ahead_price"]).tolist()
    )

    savings = total_cost_baseline - total_cost_simulated
    
    print(f"\n  Baseline Cost (No Battery): €{total_cost_baseline:.2f}")
    print(f"  Simulated Cost (With Battery): €{total_cost_simulated:.2f}")
    print(f"  Total Savings: €{savings:.2f}")

    # 5. E2E ASSERTIONS
    
    # Check Data Integrity
    assert not merged_df['adj_prod'].isnull().any(), "Simulation resulted in NaNs in production"
    assert not merged_df['adj_cons'].isnull().any(), "Simulation resulted in NaNs in consumption"

    # Check Energy Conservation
    delta_soc = bat.get_soc_kwh() - initial_soc_kwh
    net_initial = merged_df['teruglevering'].sum() - merged_df['verbruik'].sum()
    net_final = merged_df['adj_prod'].sum() - merged_df['adj_cons'].sum()
    losses = net_initial - net_final - delta_soc
    
    assert losses >= -1e-6, f"Energy created! Losses: {losses}"
    assert losses > 0, "Zero losses found (unrealistic for 90% efficiency)"

    # Check Business Logic (Savings should be positive for this specific price-based scenario)
    assert total_cost_simulated < total_cost_baseline, f"Battery increased costs! Savings: {savings}"
    assert savings > 10, "Savings are unexpectedly low (< €10) for a full year simulation"

    # 6. REGRESSION CHECK (Compare against baseline)
    baseline_path = Path("tests/simulation_baseline.csv")
    if baseline_path.exists():
        baseline_df = pd.read_csv(baseline_path)
        # Convert timestamps to match types if necessary
        baseline_df['timestamp'] = pd.to_datetime(baseline_df['timestamp'])
        
        # We compare the critical simulation columns
        cols_to_compare = ['adj_prod', 'adj_cons', 'battery_soc']
        
        pd.testing.assert_frame_equal(
            merged_df[cols_to_compare].reset_index(drop=True),
            baseline_df[cols_to_compare].reset_index(drop=True),
            atol=1e-5
        )
        
        # Financial Regression (Hardcoded based on 2025 energy tax and current data)
        # These values reflect the "Fixed" logic with Net Metering OFF
        expected_baseline_cost = 443.30
        expected_simulated_cost = 412.57
        
        assert abs(total_cost_baseline - expected_baseline_cost) < 0.05, f"Baseline cost changed! Got {total_cost_baseline}, expected {expected_baseline_cost}"
        assert abs(total_cost_simulated - expected_simulated_cost) < 0.05, f"Simulated cost changed! Got {total_cost_simulated}, expected {expected_simulated_cost}"

        print("  Regression Check: PASSED (Matches baseline and financial expectations)")
    else:
        print("  Regression Check: SKIPPED (No baseline file found)")
