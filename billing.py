from models import SimulationResult
from energy_providers import Provider, VAT_RATE, get_energy_tax_excl_vat, get_grid_operator_fee, get_tax_discount

class BillingEngine:
    """
    Calculates costs and savings based on simulation results and provider configurations.
    """
    def __init__(self, provider: Provider, dso: str = 'Enexis', year: int = 2025):
        self.provider = provider
        self.dso = dso
        self.year = year

    def calculate_bill(self, result: SimulationResult) -> float:
        """
        Calculates the total dynamic bill for a simulation result.
        """
        fixed_costs = self.provider.get_fixed_costs(year=self.year)
        
        # Fallback to original columns if adjusted ones don't exist
        cons_col = "adjusted_consumption" if "adjusted_consumption" in result.df.columns else "verbruik"
        prod_col = "adjusted_production" if "adjusted_production" in result.df.columns else "teruglevering"
        
        flexible_costs = self.provider.calculate_flexible_costs(
            consumption_kwh=result.df[cons_col].tolist(),
            feed_in_kwh=result.df[prod_col].tolist(),
            prices_eur_per_kwh_excl_vat=result.df["day_ahead_price"].tolist()
        )
        
        return fixed_costs + flexible_costs

    def calculate_savings(self, baseline_result: SimulationResult, simulated_result: SimulationResult) -> float:
        """
        Calculates savings between a baseline (no battery) and a simulated (with battery) result.
        """
        cost_baseline = self.provider.calculate_flexible_costs(
            consumption_kwh=baseline_result.df["verbruik"].tolist(),
            feed_in_kwh=baseline_result.df["teruglevering"].tolist(),
            prices_eur_per_kwh_excl_vat=baseline_result.df["day_ahead_price"].tolist()
        )
        
        cost_simulated = self.provider.calculate_flexible_costs(
            consumption_kwh=simulated_result.df["adjusted_consumption"].tolist(),
            feed_in_kwh=simulated_result.df["adjusted_production"].tolist(),
            prices_eur_per_kwh_excl_vat=simulated_result.df["day_ahead_price"].tolist()
        )
        
        return cost_baseline - cost_simulated
