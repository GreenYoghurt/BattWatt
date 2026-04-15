class Controller_empty():
    def __init__(self):
        pass

    def step(self, production, consumption, duration_hours=0.25):
        return production, consumption # to_battery, from_battery


class Controller_PV():
    def __init__(self, Battery):
        self.Battery = Battery

    def step(self, production, consumption, duration_hours=0.25):
        # Always net production and consumption first.
        # This ensures that we first use our own production to cover our own consumption.
        common = min(production, consumption)
        to_battery = common
        from_battery = common

        # get battery state and parameters
        soc_kwh = self.Battery.get_soc_kwh()
        remaining_capacity = self.Battery.capacity_kwh - soc_kwh
        max_charge_energy = self.Battery.max_charge_kw * duration_hours
        max_discharge_energy = self.Battery.max_discharge_kw * duration_hours
        efficiency_charging = self.Battery.efficiency_charging
        efficiency_discharging = self.Battery.efficiency_discharging

        net_energy = production - consumption  # kWh

        # if excess production, charge the battery
        if net_energy > 0:
            # We want to put net_energy into the battery. 
            # The maximum input energy the battery can take is limited by its power and its remaining capacity.
            max_input_from_capacity = remaining_capacity / efficiency_charging
            charge_amount = min(net_energy, max_input_from_capacity) # account for remaining capacity    
            charge_amount = min(charge_amount, max_charge_energy) # account for power limit
            to_battery += charge_amount

        # if deficit, discharge the battery
        elif net_energy < 0:
            # We want to take -net_energy from the battery.
            # The maximum output energy the battery can provide is limited by its power and its stored energy.
            max_output_from_soc = soc_kwh * efficiency_discharging
            discharge_amount = min(-net_energy, max_output_from_soc) # account for remaining capacity
            discharge_amount = min(discharge_amount, max_discharge_energy) # account for power limit
            from_battery += discharge_amount

        return to_battery, from_battery