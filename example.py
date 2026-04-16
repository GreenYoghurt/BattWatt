import os
import pandas as pd
from pathlib import Path
from data_loader import load_meter_data_HomeWizzard, load_price_data, merge_data
from plotter import plot_usage_and_price, plot_battery_effect, show
from energy_providers import get_providers
from battery import get_battery
from controller_PV import Controller_PV
from controller_price import Controller_price
from simulator import Simulator
from billing import BillingEngine

# Set CWD to the directory containing the script
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# CONFIGURATIE
# ---------------------------------------------------------------------------

PATH_PRICE = Path("../example/day_ahead_2025.xlsx")
PATH_DATA = Path("../example/P1e-2025-1-01-2026-1-01.csv")

def check_energy_conservation(result):
    print(f"Energy Balance Check:")
    print(f"  Initial Net: {result.total_production_kwh - result.total_consumption_kwh:.3f} kWh")
    print(f"  Final Net:   {result.total_adjusted_production_kwh - result.total_adjusted_consumption_kwh:.3f} kWh")
    print(f"  Delta SoC:   {result.delta_soc_kwh:.3f} kWh")
    
    losses = (result.total_production_kwh - result.total_consumption_kwh) - \
             (result.total_adjusted_production_kwh - result.total_adjusted_consumption_kwh) - \
             result.delta_soc_kwh
    
    print(f"  Losses:      {losses:.3f} kWh")
    
    if losses < -1e-6:
        print(f"  WARNING: Energy conservation violated! Negative losses: {losses:.6f}")
    else:
        print(f"  Energy conservation passed (Losses >= 0).")

def main() -> None:
    # 1. Data inlezen
    price_df = load_price_data(PATH_PRICE)
    meter_df = load_meter_data_HomeWizzard(PATH_DATA)

    # 2. Samenvoegen & Pre-processing
    merged_df = merge_data(meter_df, price_df, tolerance="15min")
    merged_df['day_ahead_price'] = merged_df['day_ahead_price']/1000  # EUR/MWh -> EUR/kWh
    merged_df.set_index("timestamp", drop=False, inplace=True)

    provider = get_providers()["Zonneplan"]
    billing = BillingEngine(provider)

    # 3. Setup Battery & Controller
    battery = get_battery("Bliq_5kwh")
    # controller = Controller_PV(battery)
    controller = Controller_price(battery, merged_df)

    # 4. Run Simulation
    simulator = Simulator(battery, controller)
    result = simulator.run(merged_df)

    # 5. Calculate Financials
    # Baseline simulation (no battery) is just the input data
    # We create a dummy result for the baseline to use the billing engine
    from models import SimulationResult
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
    
    print(f"\nTotal Savings: €{savings:.2f}")
    
    show()

if __name__ == "__main__":
    main()
