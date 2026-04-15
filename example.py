import os
import numpy as np
import pandas as pd
from data_loader import load_meter_data_HomeWizzard, load_price_data, merge_data
from plotter import plot_usage_and_price, plot_battery_effect, show
from pathlib import Path
from energy_providers import get_providers
from tqdm import tqdm
from battery import Battery, get_battery
from controller_PV import Controller_PV, Controller_empty
from controller_price import Controller_price

# Set CWD to the directory containing the script
os.chdir(os.path.dirname(os.path.abspath(__file__)))

"""
Combineer verbruiks-/terugleveringsdata met day-ahead prijzen en maak één plot.

- X-as: tijd (15-min intervallen)
- Linker Y-as: verbruik en teruglevering (kWh)
- Rechter Y-as: day-ahead prijs (EUR/MWh)
"""


# ---------------------------------------------------------------------------
# CONFIGURATIE
# ---------------------------------------------------------------------------

# Pas deze paden aan naar wens
PATH_PRICE = Path("../example/day_ahead_2025.xlsx")
PATH_DATA = Path("../example/P1e-2025-1-01-2026-1-01.csv")

# ---------------------------------------------------------------------------
# CHECKS
# ---------------------------------------------------------------------------
def check_energy_conservation(df, battery_capacity_kwh):
    initial_soc_kwh = 0 # Assuming starts at 0
    final_soc_kwh = (df['battery_soc'].iloc[-1] / 100) * battery_capacity_kwh
    
    total_production = df['teruglevering'].sum()
    total_consumption = df['verbruik'].sum()
    
    total_adjusted_production = df['adjusted_production'].sum()
    total_adjusted_consumption = df['adjusted_consumption'].sum()
    
    # Net energy before battery
    net_initial = total_production - total_consumption
    # Net energy after battery (to grid)
    net_final = total_adjusted_production - total_adjusted_consumption
    
    # Change in storage
    delta_storage = final_soc_kwh - initial_soc_kwh
    
    # In a perfect world: net_initial = net_final + delta_storage + losses
    # So: net_initial - net_final - delta_storage should be positive (losses)
    imbalance = net_initial - net_final - delta_storage
    
    print(f"Energy Balance Check:")
    print(f"  Initial Net: {net_initial:.3f} kWh")
    print(f"  Final Net:   {net_final:.3f} kWh")
    print(f"  Delta SoC:   {delta_storage:.3f} kWh")
    print(f"  Losses:      {imbalance:.3f} kWh")
    
    if imbalance < -1e-6:
        print(f"  WARNING: Energy conservation violated! Negative losses: {imbalance:.6f}")
    else:
        print(f"  Energy conservation passed (Losses >= 0).")



# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    # Data inlezen
    price_df = load_price_data(PATH_PRICE)
    meter_df = load_meter_data_HomeWizzard(PATH_DATA)

    # Samenvoegen
    merged_df = merge_data(meter_df, price_df, tolerance="15min")
    merged_df['day_ahead_price'] = merged_df['day_ahead_price']/1000  # EUR/MWh -> EUR/kWh
    merged_df.set_index("timestamp", drop=False, inplace=True)

    Provider = get_providers()["Zonneplan"]

    # Provider.calculate_dynamic_bill(
    #     consumption_kwh=merged_df["verbruik"].tolist(),
    #     feed_in_kwh=merged_df["teruglevering"].tolist(),
    #     prices_eur_per_kwh_excl_vat=(merged_df["day_ahead_price"]).tolist()  # EUR/MWh -> EUR/kWh
    # )
    total_cost1 = Provider.calculate_flexible_costs(
        consumption_kwh=merged_df["verbruik"].tolist(),
        feed_in_kwh=merged_df["teruglevering"].tolist(),
        prices_eur_per_kwh_excl_vat=(merged_df["day_ahead_price"]).tolist()  # EUR/MWh -> EUR/kWh
    )

    merged_df['prijs_inkoop'] = (merged_df['day_ahead_price'] + Provider.buying_fee) * (1 + 0.21)
    merged_df['prijs_verkoop'] = (merged_df['day_ahead_price'] - Provider.selling_fee) * (1 + 0.21)

    # Plotten
    plot_usage_and_price(merged_df)


    ### batterij simulatie ###
    Battery = get_battery("Bliq_5kwh")

    # controller = Controller_PV(Battery)
    controller = Controller_price(Battery, merged_df)

    for index, row in merged_df.iterrows():
        production = row['teruglevering']
        consumption = row['verbruik']

        
        to_battery, from_battery = controller.step(production, consumption, datetime_index=index, duration_hours=0.25)
        
        to_grid, from_grid = Battery.step(to_battery, from_battery, duration_hours=0.25)
        

        # calculate production and consumption after battery adjustment
        net_grid_energy = (production - consumption) - (to_battery - from_battery) + (to_grid - from_grid)
        if net_grid_energy >= 0:
            production2 = net_grid_energy
            consumption2 = 0
        else:
            production2 = 0
            consumption2 = -net_grid_energy

        soc = Battery.get_soc()
        merged_df.at[index, 'battery_soc'] = soc
        merged_df.at[index, 'adjusted_production'] = production2
        merged_df.at[index, 'adjusted_consumption'] = consumption2
    
    
    plot_battery_effect(merged_df)

    check_energy_conservation(merged_df, Battery.capacity_kwh)

    print("\n")
    # Provider.calculate_dynamic_bill(
    #     consumption_kwh=merged_df["adjusted_consumption"].tolist(),
    #     feed_in_kwh=merged_df["adjusted_production"].tolist(),
    #     prices_eur_per_kwh_excl_vat=(merged_df["day_ahead_price"]).tolist()  # EUR/MWh -> EUR/kWh
    # )
    total_cost2 = Provider.calculate_flexible_costs(
        consumption_kwh=merged_df["adjusted_consumption"].tolist(),
        feed_in_kwh=merged_df["adjusted_production"].tolist(),
        prices_eur_per_kwh_excl_vat=(merged_df["day_ahead_price"]).tolist()  # EUR/MWh -> EUR/kWh
    )

    print(f"\ntotal cost without battery: €{total_cost1:.2f}, total cost with battery: €{total_cost2:.2f}, savings: €{total_cost1 - total_cost2:.2f}")
    

    show()



if __name__ == "__main__":
    main()
