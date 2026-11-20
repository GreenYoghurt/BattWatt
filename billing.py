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

    def calculate_bill_breakdown(self, result: SimulationResult) -> dict:
        """Returns itemised cost components for the simulation result."""
        year = self.year
        import energy_providers as ep

        # Use the same full-year fixed costs as calculate_bill() so the breakdown totals match
        abonnementskosten = self.provider.subscription_cost
        netbeheerskosten = ep.get_grid_operator_fee(self.dso)
        belastingvermindering = ep.get_tax_discount(year)

        cons_col = "adjusted_consumption" if "adjusted_consumption" in result.df.columns else "verbruik"
        prod_col = "adjusted_production" if "adjusted_production" in result.df.columns else "teruglevering"

        flex = self.provider.calculate_costs_breakdown(
            consumption_kwh=result.df[cons_col].tolist(),
            feed_in_kwh=result.df[prod_col].tolist(),
            datetime_index=result.df.index,
            prices_eur_per_kwh_excl_vat=result.df["day_ahead_price"].tolist(),
        )

        total = (
            abonnementskosten
            + netbeheerskosten
            - belastingvermindering
            + flex["total_flexible"]
        )

        breakdown = {
            "abonnementskosten": abonnementskosten,
            "netbeheerskosten": netbeheerskosten,
            "belastingvermindering": belastingvermindering,
            "total_consumption_kwh": flex["total_consumption_kwh"],
            "total_feed_in_kwh": flex["total_feed_in_kwh"],
            "total": total,
            "contract_type": self.provider.contract_type,
            # Unit rates for display
            "tarief_abonnementskosten": self.provider.subscription_cost,
            "tarief_netbeheerskosten": ep.get_grid_operator_fee(self.dso),
            "tarief_belastingvermindering": ep.get_tax_discount(year),
        }

        if self.provider.contract_type == "fixed":
            breakdown.update({
                "normaal_kosten": flex["normaal_kosten"],
                "dal_kosten": flex["dal_kosten"],
                "teruglevering_opbrengst": flex["teruglevering_opbrengst"],
                "normaal_kwh": flex["normaal_kwh"],
                "dal_kwh": flex["dal_kwh"],
                "tarief_normaal_tarief": self.provider.normaal_tarief,
                "tarief_dal_tarief": self.provider.dal_tarief,
                "tarief_terugleververgoeding": self.provider.terugleververgoeding,
            })
        else:
            breakdown.update({
                "marktprijs_inkoop": flex["marktprijs_inkoop"],
                "energiebelasting": flex["energiebelasting"],
                "leveranciersopslag_inkoop": flex["leveranciersopslag_inkoop"],
                "leveranciersopslag_verkoop": flex["leveranciersopslag_verkoop"],
                "teruglevering_opbrengst": flex["teruglevering_opbrengst"],
                "energiebelasting_kwh": flex["energiebelasting_kwh"],
                "tarief_energiebelasting_per_kwh": ep.get_energy_tax_excl_vat(year) * (1 + ep.VAT_RATE),
                "tarief_leveranciersopslag_inkoop_per_kwh": self.provider.buying_fee,
                "tarief_leveranciersopslag_verkoop_per_kwh": self.provider.selling_fee,
            })

        return breakdown

    def calculate_bill(self, result: SimulationResult) -> float:
        """
        Calculates the total bill for a simulation result.
        """
        return self.calculate_bill_breakdown(result)["total"]

    def calculate_savings(self, baseline_result: SimulationResult, simulated_result: SimulationResult) -> float:
        """
        Calculates savings between a baseline (no battery) and a simulated (with battery) result.
        """
        cost_baseline = self.provider.calculate_costs_total(
            consumption_kwh=baseline_result.df["verbruik"].tolist(),
            feed_in_kwh=baseline_result.df["teruglevering"].tolist(),
            datetime_index=baseline_result.df.index,
            prices_eur_per_kwh_excl_vat=baseline_result.df["day_ahead_price"].tolist()
        )

        cost_simulated = self.provider.calculate_costs_total(
            consumption_kwh=simulated_result.df["adjusted_consumption"].tolist(),
            feed_in_kwh=simulated_result.df["adjusted_production"].tolist(),
            datetime_index=simulated_result.df.index,
            prices_eur_per_kwh_excl_vat=simulated_result.df["day_ahead_price"].tolist()
        )

        return cost_baseline - cost_simulated


def fixed_costs_from_breakdown(breakdown: dict) -> float:
    """Fixed annual part of a bill breakdown: abonnement + netbeheer − belastingvermindering."""
    return (breakdown["abonnementskosten"]
            + breakdown["netbeheerskosten"]
            - breakdown["belastingvermindering"])


def compare_providers(providers, baseline_result: SimulationResult, simulated_results,
                      dso: str = "Enexis", year: int = 2025) -> list:
    """Bills the same baseline and battery simulations under several providers.

    The battery dispatch is computed once (by the caller) and reused for every
    provider: only the billing differs. Returns one dict per provider, in the
    order given, with the baseline bill and, per simulated result (same order
    as ``simulated_results``), the bill with battery and the savings. Every
    amount is given both including (``cost_*``) and excluding (``*_variable``)
    the fixed annual costs; savings are the same either way, because the fixed
    costs of one provider are identical with and without a battery.
    """
    comparison = []
    for provider in providers:
        engine = BillingEngine(provider, dso=dso, year=year)
        bd_base = engine.calculate_bill_breakdown(baseline_result)
        cost_base = bd_base["total"]
        cost_base_var = cost_base - fixed_costs_from_breakdown(bd_base)

        results = []
        for result in simulated_results:
            bd = engine.calculate_bill_breakdown(result)
            cost = bd["total"]
            results.append({
                "cost_total": cost,
                "cost_variable": cost - fixed_costs_from_breakdown(bd),
                "savings": cost_base - cost,
                "breakdown": bd,
            })

        comparison.append({
            "name": provider.name,
            "contract_type": provider.contract_type,
            "cost_baseline": cost_base,
            "cost_baseline_variable": cost_base_var,
            "breakdown_baseline": bd_base,
            "results": results,
        })
    return comparison
