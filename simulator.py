import numpy as np
import pandas as pd
from models import SimulationResult
from tqdm import tqdm

class Simulator:
    """
    Unified simulation engine that runs a battery and controller over 
    a dataset of production and consumption.
    """
    def __init__(self, battery, controller):
        self.battery = battery
        self.controller = controller

    def run(self, df: pd.DataFrame, duration_hours: float = 0.25, progress_callback: callable = None) -> SimulationResult:
        """
        Runs the simulation.
        
        Args:
            df: DataFrame containing 'teruglevering' (production) and 'verbruik' (consumption) columns.
            duration_hours: The interval duration in hours (e.g. 0.25 for 15 minutes).
            progress_callback: Optional function that takes (current_index, total_steps) for progress reporting.
            
        Returns:
            A SimulationResult object containing the updated DataFrame and totals.
        """
        # Work on a copy of the dataframe to avoid side-effects
        result_df = df.copy()
        
        # Initial SoC tracking
        initial_soc_kwh = self.battery.get_soc_kwh()
        
        # Core simulation loop
        total_steps = len(result_df)
        
        # Use tqdm if no progress_callback is provided
        iterable = result_df.iterrows()
        if progress_callback is None:
            iterable = tqdm(iterable, total=total_steps, desc="Simulating")

        soc_values, adj_prod_values, adj_cons_values = [], [], []
        for i, (index, row) in enumerate(iterable):
            if progress_callback:
                progress_callback(i + 1, total_steps)
                
            production = row['teruglevering']
            consumption = row['verbruik']
            
            # 1. Controller Step: decide energy flows
            to_battery, from_battery = self.controller.step(
                production, 
                consumption, 
                datetime_index=index, 
                duration_hours=duration_hours
            )
            
            # 2. Battery Step: apply physical limits and compute grid interaction
            to_grid, from_grid = self.battery.step(
                to_battery, 
                from_battery, 
                duration_hours=duration_hours
            )
            
            # 3. Grid Interaction Logic (The "net_grid_energy" formula)
            # This logic ensures grid flows are non-negative.
            net_grid_energy = (production - consumption) - (to_battery - from_battery) + (to_grid - from_grid)
            
            if net_grid_energy >= 0:
                adj_prod = net_grid_energy
                adj_cons = 0
            else:
                adj_prod = 0
                adj_cons = -net_grid_energy
            
            # Store results (positionally; see the column assignment below)
            soc_values.append(self.battery.get_soc())
            adj_prod_values.append(adj_prod)
            adj_cons_values.append(adj_cons)

        # Assign by position, not with `.at[index, col]`: that creates new
        # columns via a pattern pandas 4 turns into a KeyError, and with
        # duplicate timestamps (DST fall-back) it overwrote every row sharing
        # the label instead of just the current one.
        result_df['battery_soc'] = np.asarray(soc_values, dtype=float)
        result_df['adjusted_production'] = np.asarray(adj_prod_values, dtype=float)
        result_df['adjusted_consumption'] = np.asarray(adj_cons_values, dtype=float)

        # Final totals
        final_soc_kwh = self.battery.get_soc_kwh()
        total_cycles = self.battery.total_discharged_kwh / self.battery.capacity_kwh if self.battery.capacity_kwh > 0 else 0
        
        return SimulationResult(
            df=result_df,
            total_production_kwh=df['teruglevering'].sum(),
            total_consumption_kwh=df['verbruik'].sum(),
            total_adjusted_production_kwh=result_df['adjusted_production'].sum(),
            total_adjusted_consumption_kwh=result_df['adjusted_consumption'].sum(),
            final_soc_pct=self.battery.get_soc(),
            final_soc_kwh=final_soc_kwh,
            delta_soc_kwh=final_soc_kwh - initial_soc_kwh,
            total_cycles=total_cycles
        )
