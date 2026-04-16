import pytest
import numpy as np
import pandas as pd
from pathlib import Path
from data_loader import load_meter_data_HomeWizzard, load_price_data, merge_data
from energy_providers import get_providers
from battery import Battery, get_battery
from controller_PV import Controller_PV

# Paths to the real data
PATH_PRICE = Path("../example/day_ahead_2025.xlsx")
PATH_DATA = Path("../example/P1e-2025-1-01-2026-1-01.csv")

@pytest.mark.skipif(not PATH_PRICE.exists() or not PATH_DATA.exists(), 
                    reason="Real data files not found in ../example/")
def test_battwatt_pv_controller_e2e():
    """
    E2E test specifically for the Controller_PV (PV-priority) strategy.
    """
    # 1. LOAD & MERGE
    price_df = load_price_data(PATH_PRICE)
    meter_df = load_meter_data_HomeWizzard(PATH_DATA)
    merged_df = merge_data(meter_df, price_df, tolerance="15min")
    merged_df['day_ahead_price'] = merged_df['day_ahead_price']/1000 
    merged_df.set_index("timestamp", drop=False, inplace=True)

    # 2. INITIAL FINANCIALS
    Provider = get_providers()["Zonneplan"]
    # Ensure test uses the current repository setting (net_metering=True)
    Provider.net_metering = True 
    
    total_cost_baseline = Provider.calculate_flexible_costs(
        consumption_kwh=merged_df["verbruik"].tolist(),
        feed_in_kwh=merged_df["teruglevering"].tolist(),
        prices_eur_per_kwh_excl_vat=(merged_df["day_ahead_price"]).tolist()
    )

    # 3. RUN SIMULATION (PV Controller)
    bat = get_battery("Bliq_5kwh")
    controller = Controller_PV(bat)
    initial_soc_kwh = bat.get_soc_kwh()
    
    for index, row in merged_df.iterrows():
        prod, cons = row['teruglevering'], row['verbruik']
        
        to_bat, from_bat = controller.step(prod, cons, datetime_index=index, duration_hours=0.25)
        to_grid_bat, from_grid_bat = bat.step(to_bat, from_bat, duration_hours=0.25)
        
        # Grid interaction formula
        net_grid_energy = (prod - cons) - (to_bat - from_bat) + (to_grid_bat - from_grid_bat)
        if net_grid_energy >= 0:
            prod2, cons2 = net_grid_energy, 0
        else:
            prod2, cons2 = 0, -net_grid_energy

        merged_df.at[index, 'adj_prod'] = prod2
        merged_df.at[index, 'adj_cons'] = cons2

    # 4. FINAL FINANCIALS
    total_cost_simulated = Provider.calculate_flexible_costs(
        consumption_kwh=merged_df["adj_cons"].tolist(),
        feed_in_kwh=merged_df["adj_prod"].tolist(),
        prices_eur_per_kwh_excl_vat=(merged_df["day_ahead_price"]).tolist()
    )

    savings = total_cost_baseline - total_cost_simulated
    
    # 5. ASSERTIONS
    # With PV controller and Net Metering = True, savings should be positive but potentially different from Price controller
    assert savings > 0, f"PV Controller should generate savings! Got {savings}"
    
    # Energy Conservation
    delta_soc = bat.get_soc_kwh() - initial_soc_kwh
    net_initial = merged_df['teruglevering'].sum() - merged_df['verbruik'].sum()
    net_final = merged_df['adj_prod'].sum() - merged_df['adj_cons'].sum()
    losses = net_initial - net_final - delta_soc
    assert losses >= -1e-6, f"Energy conservation violated: {losses}"

    print(f"\nPV Controller E2E Results:")
    print(f"  Baseline: €{total_cost_baseline:.2f}")
    print(f"  Simulated: €{total_cost_simulated:.2f}")
    print(f"  Savings: €{savings:.2f}")

    # 6. FINANCIAL REGRESSION (Hardcoded for PV Controller + Net Metering)
    expected_baseline_cost = 254.83
    expected_simulated_cost = 176.89
    
    assert abs(total_cost_baseline - expected_baseline_cost) < 0.05, f"Baseline cost mismatch! Got {total_cost_baseline}, expected {expected_baseline_cost}"
    assert abs(total_cost_simulated - expected_simulated_cost) < 0.05, f"Simulated PV cost mismatch! Got {total_cost_simulated}, expected {expected_simulated_cost}"
    
    print("  Regression Check: PASSED (PV Financials)")
