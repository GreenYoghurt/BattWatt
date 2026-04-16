import os
import pandas as pd
from pathlib import Path
from data_loader import load_meter_data_HomeWizzard, load_price_data, merge_data
from plotter import plot_usage_and_price, plot_battery_effect, show
from energy_providers import get_providers
from battery import get_battery
from controllers.controller_MPC import Controller_MPC
from simulator import Simulator
from billing import BillingEngine
from models import SimulationResult

# Set CWD to the directory containing the script
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# CONFIGURATIE
# ---------------------------------------------------------------------------

PATH_PRICE = Path("../example/day_ahead_2025.xlsx")
PATH_DATA = Path("../example/P1e-2025-1-01-2026-1-01.csv")

def check_energy_conservation(result):
    print(f"Energy Balance Check:")
    losses = (result.total_production_kwh - result.total_consumption_kwh) - \
             (result.total_adjusted_production_kwh - result.total_adjusted_consumption_kwh) - \
             result.delta_soc_kwh
    print(f"  Losses: {losses:.3f} kWh")
    if losses < -1e-6:
        print(f"  WARNING: Energy conservation violated! {losses:.6f}")
    else:
        print(f"  Energy conservation passed.")

def main() -> None:
    # 1. Data inlezen
    print("Loading data...")
    price_df = load_price_data(PATH_PRICE)
    meter_df = load_meter_data_HomeWizzard(PATH_DATA)

    # 2. Samenvoegen & Pre-processing
    merged_df = merge_data(meter_df, price_df, tolerance="15min")
    merged_df['day_ahead_price'] = merged_df['day_ahead_price']/1000  # EUR/MWh -> EUR/kWh
    merged_df.set_index("timestamp", drop=False, inplace=True)

    provider = get_providers()["Zonneplan"]
    billing = BillingEngine(provider)

    merged_df['prijs_inkoop'] = (merged_df['day_ahead_price'] + provider.buying_fee) * (1 + 0.21)
    merged_df['prijs_verkoop'] = (merged_df['day_ahead_price'] - provider.selling_fee) * (1 + 0.21)

    # 3. Setup Battery & Controller
    battery = get_battery("Bliq_10kwh_fast")
    
    print(f"Starting MPC Simulation for the full duration...")
    # horizon_hours=24 means it looks ahead 24 hours at every 15-min step
    # reoptimize_every_hours=12.0 means we only solve a new optimization once every 12 hours
    controller = Controller_MPC(battery, merged_df, provider, horizon_hours=24.0, reoptimize_every_hours=12.0)

    # 4. Run Simulation
    simulator = Simulator(battery, controller)
    result = simulator.run(merged_df)

    # 5. Calculate Financials
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

    savings = billing.calculate_savings(baseline_result, result)
    
    # 6. Output & Plotting
    plot_usage_and_price(merged_df)
    plot_battery_effect(result.df)
    
    check_energy_conservation(result)
    print(f"\nTotal Savings (MPC - Full Run): €{savings:.2f}")
    
    show()

if __name__ == "__main__":
    main()
