class Battery:
    def __init__(self, capacity_kwh, max_charge_kw, max_discharge_kw, efficiency_charging=0.98, efficiency_discharging=0.98):
        self.capacity_kwh = capacity_kwh
        self.max_charge_kw = max_charge_kw
        self.max_discharge_kw = max_discharge_kw
        self.efficiency_charging = efficiency_charging
        self.efficiency_discharging = efficiency_discharging
        self.soc_kwh = 0  # State of Charge in kWh

    def _charge(self, energy_kwh:float=0, duration_hours:float=0.25) -> float:
        power_kw = energy_kwh / duration_hours
        power_kw = min(power_kw, self.max_charge_kw)  # limit to max charge power
        remaining = max(0.0, self.capacity_kwh - self.soc_kwh)  # remaining energy capacity in battery

        energy_wanting_to_add = power_kw*duration_hours * self.efficiency_charging

        if energy_wanting_to_add > remaining:  # can only add up to remaining capacity (considering efficiency loss)
            actual_energy_added = remaining
        else:
            actual_energy_added = energy_wanting_to_add
        self.soc_kwh += actual_energy_added

        # Return actual energy consumed while charging (kWh)
        return actual_energy_added /  self.efficiency_charging


    def _discharge(self, energy_kwh:float=0, duration_hours:float=0.25):
        power_kw = energy_kwh / duration_hours
        power_kw = min(power_kw, self.max_discharge_kw)
        energy_wanting_to_remove = power_kw*duration_hours / self.efficiency_discharging

        if energy_wanting_to_remove > self.soc_kwh:
            actual_energy_removed = self.soc_kwh
        else:
            actual_energy_removed = energy_wanting_to_remove
        
        self.soc_kwh -= actual_energy_removed
    
        # Return actual energy delivered while discharging (kWh)
        return actual_energy_removed * self.efficiency_discharging
    
    def get_soc(self) -> float:
        if self.capacity_kwh <= 0:
            return 0.0
        return self.soc_kwh/self.capacity_kwh * 100  # return as percentage
    
    def get_soc_kwh(self) -> float:
        return self.soc_kwh
    
    def step(self, production, consumption, duration_hours=0.25):
        net_energy = production - consumption  # kWh

        # Excess production, charge the battery
        if net_energy > 0:
            energy_to_charge = net_energy
            actual_energy_used_for_charging = self._charge(energy_kwh=energy_to_charge, duration_hours=duration_hours)
            production = energy_to_charge - actual_energy_used_for_charging
            consumption = 0

        # Deficit, discharge the battery
        elif net_energy < 0:
            energy_needed = -net_energy
            actual_energy_provided = self._discharge(energy_kwh=energy_needed, duration_hours=duration_hours)
            consumption = energy_needed - actual_energy_provided
            production = 0

        return production, consumption  # after battery adjustment (to grid, from grid)

def get_battery(name: str) -> Battery:
    batteries = {
        "Bliq_5kwh": Battery(capacity_kwh=5, max_charge_kw=3.68, max_discharge_kw=3.68),
        "Bliq_10kwh": Battery(capacity_kwh=10, max_charge_kw=3.68, max_discharge_kw=3.68),
        "Bliq_10kwh_fast": Battery(capacity_kwh=10, max_charge_kw=5, max_discharge_kw=5),
        "Bliq_15kwh": Battery(capacity_kwh=15, max_charge_kw=5, max_discharge_kw=5),
        "Bliq_20kwh": Battery(capacity_kwh=20, max_charge_kw=8, max_discharge_kw=8),
        "Bliq_25kwh": Battery(capacity_kwh=25, max_charge_kw=8, max_discharge_kw=8),
    }
    return batteries.get(name, None)