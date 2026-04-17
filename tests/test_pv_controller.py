import pytest
import numpy as np
import pandas as pd
from pathlib import Path
from data_loader import load_meter_data_HomeWizzard, load_price_data, merge_data
from energy_providers import get_providers
from battery import Battery, get_battery
from controllers.controller_PV import Controller_PV
from simulator import Simulator
from billing import BillingEngine
from models import SimulationResult

# Paths to the real data
PATH_PRICE = Path("tests/data/day_ahead_2025.xlsx")
PATH_DATA = Path("tests/data/test_2025.csv")

def test_battwatt_pv_controller_e2e():
    """
    E2E test specifically for the Controller_PV strategy using the Simulator.
    """
    # 1. LOAD & MERGE
    price_df = load_price_data(PATH_PRICE)
    meter_df = load_meter_data_HomeWizzard(PATH_DATA)
    merged_df = merge_data(meter_df, price_df, tolerance="15min")
    merged_df['day_ahead_price'] = merged_df['day_ahead_price']/1000 
    merged_df.set_index("timestamp", drop=False, inplace=True)

    # 2. RUN SIMULATION
    bat = get_battery("Bliq_5kwh")
    controller = Controller_PV(bat)
    simulator = Simulator(bat, controller)
    result = simulator.run(merged_df)

    # 3. FINANCIALS
    provider = get_providers()["Zonneplan"]
    provider.net_metering = True # Recorded with net_metering=True
    billing = BillingEngine(provider)
    
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

    cost_baseline = billing.calculate_bill(baseline_result) - provider.get_fixed_costs()
    cost_simulated = billing.calculate_bill(result) - provider.get_fixed_costs()
    savings = cost_baseline - cost_simulated
    
    # 4. ASSERTIONS
    assert savings > 0
    assert (result.total_production_kwh - result.total_consumption_kwh) - \
           (result.total_adjusted_production_kwh - result.total_adjusted_consumption_kwh) - \
           result.delta_soc_kwh >= -1e-6

    # 5. FINANCIAL REGRESSION
    expected_baseline_cost = 254.83
    expected_simulated_cost = 176.89
    
    assert abs(cost_baseline - expected_baseline_cost) < 0.05
    assert abs(cost_simulated - expected_simulated_cost) < 0.05
