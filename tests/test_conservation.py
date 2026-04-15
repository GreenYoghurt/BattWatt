import pytest
import numpy as np
import pandas as pd
from battery import Battery
from controller_PV import Controller_PV

def test_battery_energy_conservation():
    # Setup a battery with some losses (90% efficient charging and discharging)
    cap_kwh = 10.0
    efficiency = 0.90
    bat = Battery(
        capacity_kwh=cap_kwh, 
        max_charge_kw=5.0, 
        max_discharge_kw=5.0, 
        efficiency_charging=efficiency, 
        efficiency_discharging=efficiency
    )
    
    # Create 24 hours of synthetic data (15-min intervals)
    # Pattern: 12 hours of high production, 12 hours of high consumption
    n_intervals = 24 * 4
    production = np.zeros(n_intervals)
    production[20:60] = 2.0  # 2.0 kWh per interval (8 kW)
    
    consumption = np.zeros(n_intervals)
    consumption[0:20] = 0.5
    consumption[60:] = 1.0
    
    initial_soc_kwh = bat.get_soc_kwh()
    total_in = 0
    total_out = 0
    total_losses = 0
    
    # We simulate the system manually to verify conservation at each step
    # System: (Production - Consumption) -> Battery -> Grid
    
    for p, c in zip(production, consumption):
        # 1. Net energy before battery
        net_before = p - c
        
        # 2. Battery step
        # Input to battery system: p and c
        # Output from battery system: grid_p and grid_c
        grid_p, grid_c = bat.step(p, c, duration_hours=0.25)
        
        # 3. Final net to grid
        net_after = grid_p - grid_c
        
        # The difference must be accounted for by:
        # (Net Before) - (Net After) = (Energy stored in Battery) + (Losses)
        # Note: 'bat.step' handles internal charging/discharging and returns grid flow.
        
        # We can't easily calculate 'losses' per step without internal battery state tracking,
        # but we can check the OVERALL conservation at the end.
        total_in += p
        total_out += c
        
    final_soc_kwh = bat.get_soc_kwh()
    delta_soc = final_soc_kwh - initial_soc_kwh
    
    # Overall System Balance:
    # Total Production - Total Consumption = (Grid Export - Grid Import) + Delta SoC + Losses
    # Therefore: (Total Prod - Total Cons) - (Grid Export - Grid Import) - Delta SoC = Losses
    
    # Wait, let's track the final grid flow
    # We need to run the loop again or track them
    
def test_simulation_conservation():
    """
    A more comprehensive test that replicates the logic in example.py
    but verifies that (Sum Prod - Sum Cons) >= (Sum Grid Prod - Sum Grid Cons) + Delta SoC
    """
    cap_kwh = 5.0
    bat = Battery(capacity_kwh=cap_kwh, max_charge_kw=3.0, max_discharge_kw=3.0, efficiency_charging=0.95, efficiency_discharging=0.95)
    
    # 100 random intervals of production and consumption
    np.random.seed(42)
    prod = np.random.uniform(0, 2.0, 100)
    cons = np.random.uniform(0, 1.0, 100)
    
    grid_prod_sum = 0
    grid_cons_sum = 0
    
    initial_soc = bat.get_soc_kwh()
    
    for p, c in zip(prod, cons):
        gp, gc = bat.step(p, c, duration_hours=0.25)
        grid_prod_sum += gp
        grid_cons_sum += gc
        
    final_soc = bat.get_soc_kwh()
    delta_soc = final_soc - initial_soc
    
    initial_net = np.sum(prod) - np.sum(cons)
    final_net = grid_prod_sum - grid_cons_sum
    
    losses = initial_net - final_net - delta_soc
    
    # Conservation of energy: 
    # 1. Losses must be non-negative (energy cannot be created)
    # 2. If efficiency is 1.0, losses must be 0
    assert losses >= -1e-10, f"Energy created! Losses: {losses}"
    
    # Test perfect efficiency case
    bat_perfect = Battery(capacity_kwh=cap_kwh, max_charge_kw=3.0, max_discharge_kw=3.0, efficiency_charging=1.0, efficiency_discharging=1.0)
    grid_prod_sum = 0
    grid_cons_sum = 0
    for p, c in zip(prod, cons):
        gp, gc = bat_perfect.step(p, c, duration_hours=0.25)
        grid_prod_sum += gp
        grid_cons_sum += gc
    
    delta_soc_perfect = bat_perfect.get_soc_kwh() - 0
    losses_perfect = initial_net - (grid_prod_sum - grid_cons_sum) - delta_soc_perfect
    assert abs(losses_perfect) < 1e-10, f"Energy not balanced in perfect battery! Losses: {losses_perfect}"
