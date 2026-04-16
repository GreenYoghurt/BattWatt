import pandas as pd
import pyomo.environ as pyo
from pyomo.opt import SolverFactory
from .controller_PV import BaseController
from energy_providers import get_energy_tax_excl_vat

class Controller_MPC(BaseController):
    """
    Model Predictive Control (MPC) strategy.
    Optimizes battery behavior over a future horizon using perfect forecasts from the simulation data.
    """
    def __init__(self, Battery, full_df: pd.DataFrame, provider, horizon_hours: float = 24.0, solver_name: str = "appsi_highs"):
        self.Battery = Battery
        self.full_df = full_df
        self.provider = provider
        self.horizon_hours = horizon_hours
        self.solver_name = solver_name
        self.energy_tax = get_energy_tax_excl_vat(2025)

    def step(self, production, consumption, datetime_index, duration_hours=0.25):
        # 1. Define Horizon
        horizon_steps = int(self.horizon_hours / duration_hours)
        start_idx = self.full_df.index.get_loc(datetime_index)
        end_idx = min(start_idx + horizon_steps, len(self.full_df))
        
        horizon_df = self.full_df.iloc[start_idx:end_idx]
        
        if len(horizon_df) <= 1:
            # Fallback if we are at the end of data
            return production, consumption

        # 2. Build Optimization Model
        model = self._build_model(horizon_df, duration_hours)
        
        # 3. Solve
        solver = SolverFactory(self.solver_name)
        try:
            results = solver.solve(model, load_solutions=True, tee=False)
        except Exception as e:
            print(f"MPC Solver failed at {datetime_index}: {e}. Falling back to default.")
            return production, consumption

        # 4. Extract First Step Decision
        first_t = model.T.first()
        to_battery_optimization = pyo.value(model.to_battery[first_t])
        from_battery_optimization = pyo.value(model.from_battery[first_t])

        # Return the optimized flows
        return to_battery_optimization, from_battery_optimization

    def _build_model(self, horizon_df, duration_hours):
        m = pyo.ConcreteModel()
        m.T = pyo.Set(initialize=list(horizon_df.index), ordered=True)
        
        # Parameters from horizon
        # Use explicit names to avoid reserved Pyomo attributes like 'load'
        consumption_dict = horizon_df['verbruik'].to_dict()
        production_dict = horizon_df['teruglevering'].to_dict()
        price_dict = horizon_df['day_ahead_price'].to_dict()
        
        m.consumption_profile = pyo.Param(m.T, initialize=consumption_dict)
        m.production_profile = pyo.Param(m.T, initialize=production_dict)
        m.price = pyo.Param(m.T, initialize=price_dict)

        # Battery Parameters
        m.Vmax = pyo.Param(initialize=self.Battery.capacity_kwh)
        m.eta_charge = pyo.Param(initialize=self.Battery.efficiency_charging)
        m.eta_discharge = pyo.Param(initialize=self.Battery.efficiency_discharging)
        m.max_charge_energy = pyo.Param(initialize=self.Battery.max_charge_kw * duration_hours)
        m.max_discharge_energy = pyo.Param(initialize=self.Battery.max_discharge_kw * duration_hours)
        m.initial_soc = pyo.Param(initialize=self.Battery.get_soc_kwh())

        # Variables (Energy in kWh)
        # to_battery/from_battery are non-negative, effectively preventing "negative consumption/production" from battery
        m.to_battery = pyo.Var(m.T, within=pyo.NonNegativeReals, bounds=(0, m.max_charge_energy))
        m.from_battery = pyo.Var(m.T, within=pyo.NonNegativeReals, bounds=(0, m.max_discharge_energy))
        m.soc = pyo.Var(m.T, within=pyo.NonNegativeReals, bounds=(0, m.Vmax))
        m.grid_import = pyo.Var(m.T, within=pyo.NonNegativeReals)
        m.grid_export = pyo.Var(m.T, within=pyo.NonNegativeReals)

        # Constraints
        def energy_balance_rule(m, t):
            # Physical balance: Production + Import + Discharge = Consumption + Export + Charge
            # This ensures all grid interaction is accounted for correctly.
            return m.production_profile[t] + m.grid_import[t] + m.from_battery[t] == m.consumption_profile[t] + m.grid_export[t] + m.to_battery[t]
        m.energy_balance = pyo.Constraint(m.T, rule=energy_balance_rule)

        def soc_update_rule(m, t):
            if t == m.T.first():
                return m.soc[t] == m.initial_soc
            prev_t = m.T.prev(t)
            return m.soc[t] == m.soc[prev_t] + (m.to_battery[prev_t] * m.eta_charge) - (m.from_battery[prev_t] / m.eta_discharge)
        m.soc_update = pyo.Constraint(m.T, rule=soc_update_rule)

        # Objective: Minimize cost
        def obj_rule(m):
            total_cost = 0
            for t in m.T:
                buy_price = m.price[t] + self.provider.buying_fee + self.energy_tax
                sell_price = m.price[t] - self.provider.selling_fee
                total_cost += (m.grid_import[t] * buy_price) - (m.grid_export[t] * sell_price)
            return total_cost
        
        m.obj = pyo.Objective(rule=obj_rule, sense=pyo.minimize)
        
        return m
