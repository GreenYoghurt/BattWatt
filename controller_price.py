import pandas as pd
import numpy as np

class Controller_price:
    def __init__(self, Battery, prices_df: pd.DataFrame):
        self.Battery = Battery
        self.prices_df = prices_df
        
        # Pre-calculate thresholds for the entire period
        # We use a rolling window of 24 hours to determine "cheap" and "expensive" relative to the current day/period
        # Shift -12h to center the window approximately, or just look at the day.
        # Simple approach: Daily quantiles.
        
        # Ensure index is datetime
        if not isinstance(self.prices_df.index, pd.DatetimeIndex):
            # Try to infer or assume it's set correctly by caller
            pass

        # Calculate dynamic thresholds
        # Low price (Charge): Bottom 25% of the last 24h
        # High price (Discharge): Top 25% of the last 24h
        # We use 'prijs_inkoop' for buying (charging) and 'prijs_verkoop' for selling (discharging) signals if available,
        # otherwise fallback to 'day_ahead_price'.
        
        col_price = 'day_ahead_price'
        if 'day_ahead_price' not in prices_df.columns:
             # Fallback or error
             print("Warning: 'day_ahead_price' not found in dataframe. Controller might fail.")

        # Rolling quantiles
        # We use a centered window or backward looking? 
        # For Day-Ahead, we know the future. So we can use a centered window or daily groups.
        # Let's use daily groups for simplicity and robustness.
        
        # Create a temporary column for grouping by date
        temp_df = prices_df.copy()
        temp_df['date'] = temp_df.index.date
        
        # Calculate daily quantiles
        daily_stats = temp_df.groupby('date')[col_price].agg(
            low_threshold=lambda x: x.quantile(0.20),
            high_threshold=lambda x: x.quantile(0.80)
        )
        
        # Map back to the dataframe
        temp_df = temp_df.merge(daily_stats, left_on='date', right_index=True, how='left')
        
        self.prices_df['threshold_low'] = temp_df['low_threshold']
        self.prices_df['threshold_high'] = temp_df['high_threshold']


    def step(self, production, consumption, datetime_index, duration_hours=0.25):
        # 1. Netting first (Self-consumption)
        # It's always efficient to use own PV for own consumption
        common = min(production, consumption)
        to_battery = common
        from_battery = common # These cancel out in the final accounting, representing direct use
        
        # Remaining energy
        net_production = production - common
        net_consumption = consumption - common
        
        # 2. Get Battery State
        soc_kwh = self.Battery.get_soc_kwh()
        remaining_capacity = self.Battery.capacity_kwh - soc_kwh
        max_charge_energy = self.Battery.max_charge_kw * duration_hours
        max_discharge_energy = self.Battery.max_discharge_kw * duration_hours
        
        # 3. Get Price Signals
        # We need to look up the pre-calculated thresholds for this specific time
        try:
            row = self.prices_df.loc[datetime_index]
            current_price = row['day_ahead_price']
            threshold_low = row['threshold_low']
            threshold_high = row['threshold_high']
        except KeyError:
            # Fallback if index not found
            return to_battery, from_battery

        # 4. Decision Logic
        
        # Strategy:
        # - If Price <= Low Threshold: CHARGE (Fill battery)
        # - If Price >= High Threshold: DISCHARGE (Empty battery)
        # - Otherwise: IDLE (or just store excess PV?)
        
        # NOTE: With Dynamic Contracts, we might want to charge from GRID if price is super low.
        # And discharge to GRID if price is super high.
        
        # Determine Grid Charge/Discharge desire
        desired_charge = 0.0
        desired_discharge = 0.0
        
        if current_price <= threshold_low:
            # Price is cheap: Charge as much as possible
            desired_charge = max_charge_energy
            
        elif current_price >= threshold_high:
            # Price is high: Discharge as much as possible
            desired_discharge = max_discharge_energy
            
        # 5. Apply Logic with Priorities
        
        # CHARGING LOGIC
        if desired_charge > 0:
            # We want to charge.
            # Source 1: Excess PV (Free)
            # Source 2: Grid (Cheap)
            
            # Use excess PV first
            charge_from_pv = min(net_production, desired_charge)
            
            # Remaining charge needed
            remaining_charge_needed = desired_charge - charge_from_pv
            
            # Charge from Grid? Yes, if price is low, we buy from grid.
            # But we must respect battery capacity
            
            total_charge_input = charge_from_pv + remaining_charge_needed
            
            # Check physical limits (Capacity)
            max_input_from_capacity = remaining_capacity / self.Battery.efficiency_charging
            
            actual_charge = min(total_charge_input, max_input_from_capacity)
            actual_charge = min(actual_charge, max_charge_energy)
            
            to_battery += actual_charge
            
            # Note: 'to_battery' is the total energy SENT to the battery.
            # The 'net_production' part is covered. The rest comes from Grid implicitly in the main loop calculations.
            
        
        # DISCHARGING LOGIC
        elif desired_discharge > 0:
            # We want to discharge.
            # Sink 1: Home Consumption (Avoid buying expensive grid power)
            # Sink 2: Grid (Sell for high price)
            
            # Use battery to cover consumption first?
            # Yes, discharging to home saves 'price_inkoop'. Discharging to grid earns 'price_verkoop'.
            # Usually inkoop > verkoop due to tax. So covering consumption is priority.
            
            # Cover remaining consumption
            discharge_for_home = min(net_consumption, desired_discharge)
            
            # Remaining discharge ability
            remaining_discharge_ability = desired_discharge - discharge_for_home
            
            # Discharge to Grid? Yes.
            total_discharge_output = discharge_for_home + remaining_discharge_ability
            
            # Check physical limits (SoC)
            max_output_from_soc = soc_kwh * self.Battery.efficiency_discharging
            
            actual_discharge = min(total_discharge_output, max_output_from_soc)
            actual_discharge = min(actual_discharge, max_discharge_energy)
            
            from_battery += actual_discharge

        else:
            # Price is moderate.
            # Standard "Self-Consumption" mode?
            # Or "Hold"? 
            # Usually, if we have excess PV, we should charge it rather than export (if export price is low).
            # If we have deficit, we should discharge (if import price is high-ish).
            
            # Let's implement basic Self-Consumption for the "Mid-Price" range.
            # (i.e. don't aggressively grid charge/discharge, but buffer PV)
            
            if net_production > 0:
                # Excess PV: Charge it
                max_input_from_capacity = remaining_capacity / self.Battery.efficiency_charging
                charge_amount = min(net_production, max_input_from_capacity)
                charge_amount = min(charge_amount, max_charge_energy)
                to_battery += charge_amount
                
            elif net_consumption > 0:
                # Deficit: Discharge it
                max_output_from_soc = soc_kwh * self.Battery.efficiency_discharging
                discharge_amount = min(net_consumption, max_output_from_soc)
                discharge_amount = min(discharge_amount, max_discharge_energy)
                from_battery += discharge_amount
        
        return to_battery, from_battery
