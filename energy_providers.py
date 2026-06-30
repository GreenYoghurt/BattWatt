VAT_RATE = 0.21  # 21% btw

def get_energy_tax_excl_vat(year: int=2025) -> float:
    # energiebelasting + vermindering (alle bedragen EXCL. btw)
    if year == 2025:
        return 0.1088  # €/kWh (schijf 1 en 2 samen)
    else:
        raise ValueError("Energy tax for the specified year is not available.")

def get_tax_discount(year: int=2025) -> float:
    # tax discount for the whole year
    if year == 2025:
        return 635.19
    else:
        raise ValueError("Tax discount for the specified year is not available.")


def get_grid_operator_fee(dso:str = 'Enexis') -> float:
    
    # 2025 tarrifs for the whole year
    if dso == 'Enexis':
        return 475.92
    elif dso == 'Liander':
        return 455.73
    elif dso == 'Stedin':
        return 473.76
    elif dso == 'Westland Infra':
        return 521.23
    elif dso == 'Coteq':
        return 407.64
    elif dso == 'RENDO':
        return 386.93
    

class Provider:
    def __init__(self, name: str, subscription_cost: float, buying_fee: float, selling_fee: float, net_metering: bool, selling_fee_net_metering: bool):
        self.name = name
        self.subscription_cost = subscription_cost  # yearly fixed cost in euros
        self.buying_fee = buying_fee          # cost per kWh bought for the client in euros
        self.selling_fee = selling_fee        # revenue per kWh sold back to the provider in euros
        self.net_metering = net_metering  # whether net metering applies
        self.selling_fee_net_metering = selling_fee_net_metering  # whether the provider allows to net meter the selling fee


    def get_fixed_costs(self, year: int=2025, time_interfal = 'yearly') -> float:

        # if year is a leap year, consider 366 days
        if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
            minutes_per_year = 366 * 24 * 60        
        else:
            minutes_per_year = 365 * 24 * 60

        # if time_interfal is a string:
        if isinstance(time_interfal, str):
            if time_interfal == 'yearly':
                time_interfal = minutes_per_year
            else:
                raise ValueError("time_interfal string value not recognized. Use 'yearly' or provide time interval in minutes as integer.")
        elif not isinstance(time_interfal, int):
            raise ValueError("time_interfal must be either a string ('yearly') or an integer representing minutes.")

        tax_discount = get_tax_discount(year) / minutes_per_year * time_interfal # tax discount per time interval in minutes
        grid_operator_fee = get_grid_operator_fee() / minutes_per_year * time_interfal # grid operator fee per time interval in minutes
        subscription_cost = self.subscription_cost / minutes_per_year * time_interfal # subscription cost per time interval in minutes

        total_fixed_cost = subscription_cost + grid_operator_fee - tax_discount

        return total_fixed_cost


    def calculate_flexible_costs_breakdown(self, consumption_kwh, feed_in_kwh, prices_eur_per_kwh_excl_vat) -> dict:
        total_consumption = float(sum(consumption_kwh))
        total_feed_in = float(sum(feed_in_kwh))

        if self.net_metering:
            marktprijs_inkoop = (1 + VAT_RATE) * sum(
                c * p for c, p in zip(consumption_kwh, prices_eur_per_kwh_excl_vat)
            )
            net_consumption = max(0.0, total_consumption - total_feed_in)
            energiebelasting = (1 + VAT_RATE) * net_consumption * get_energy_tax_excl_vat()
            energiebelasting_kwh = net_consumption
        else:
            marktprijs_inkoop = (1 + VAT_RATE) * sum(
                c * p for c, p in zip(consumption_kwh, prices_eur_per_kwh_excl_vat)
            )
            energiebelasting = (1 + VAT_RATE) * total_consumption * get_energy_tax_excl_vat()
            energiebelasting_kwh = total_consumption

        leveranciersopslag_inkoop = total_consumption * self.buying_fee
        leveranciersopslag_verkoop = total_feed_in * self.selling_fee

        teruglevering_opbrengst = (1 + VAT_RATE) * sum(
            f * p for f, p in zip(feed_in_kwh, prices_eur_per_kwh_excl_vat)
        )

        total_flexible = (
            marktprijs_inkoop
            + energiebelasting
            + leveranciersopslag_inkoop
            + leveranciersopslag_verkoop
            - teruglevering_opbrengst
        )

        return {
            "marktprijs_inkoop": marktprijs_inkoop,
            "energiebelasting": energiebelasting,
            "leveranciersopslag_inkoop": leveranciersopslag_inkoop,
            "leveranciersopslag_verkoop": leveranciersopslag_verkoop,
            "teruglevering_opbrengst": teruglevering_opbrengst,
            "total_flexible": total_flexible,
            "total_consumption_kwh": total_consumption,
            "total_feed_in_kwh": total_feed_in,
            "energiebelasting_kwh": energiebelasting_kwh,
        }

    def calculate_flexible_costs(self, consumption_kwh, feed_in_kwh, prices_eur_per_kwh_excl_vat) -> float:
        bd = self.calculate_flexible_costs_breakdown(consumption_kwh, feed_in_kwh, prices_eur_per_kwh_excl_vat)

        print(f"total_consumption: {bd['total_consumption_kwh']} kWh")
        print(f"total_feed_in: {bd['total_feed_in_kwh']} kWh")
        print(f"stroom_inkoop: {bd['marktprijs_inkoop'] + bd['energiebelasting']}")
        print(f"kosten_stroom_inkoop: {bd['leveranciersopslag_inkoop']}")
        print(f"stroom_terugleveren_niet_te_salderen: {bd['teruglevering_opbrengst']}")
        print(f"total flexible cost {bd['total_flexible']}")

        return bd["total_flexible"]

        

    def calculate_dynamic_bill(self, consumption_kwh, feed_in_kwh, prices_eur_per_kwh_excl_vat, dso: str = 'Enexis') -> float:
        netbeheerskosten = get_grid_operator_fee(dso)
        abonnementskosten = self.subscription_cost
        vermindering_belasting = get_tax_discount(year=2025)

        print("=== DYNAMIC BILL CALCULATION ===")
        print(f"netbeheerskosten: {netbeheerskosten}")
        print(f"abonnementskosten: {abonnementskosten}")
        print(f"vermindering_belasting: {vermindering_belasting}")

        total_cost = (
            netbeheerskosten
            +abonnementskosten
            -vermindering_belasting
            +self.calculate_flexible_costs(consumption_kwh, feed_in_kwh, prices_eur_per_kwh_excl_vat)
        )
        print(f"TOTAL COST FOR THE YEAR: {total_cost} EUR")
        return total_cost


def get_providers():
    providers = {}
    Zonneplan = Provider(
        name="Zonneplan",
        subscription_cost=75,  # yearly fixed cost in euros
        buying_fee=0.0199892,  # cost per kWh bought for the client in euros incl btw
        selling_fee=0.0199892,      # cost per kWh sold for the client in euros incl btw
        net_metering=False,     # whether net metering applies
        selling_fee_net_metering=True  # whether the provider allows to net meter the selling fee
    )
    providers["Zonneplan"] = Zonneplan

    return providers


if __name__ == "__main__":
    providers = get_providers()
    zonneplan = providers["Zonneplan"]
    
    zonneplan.calculate_dynamic_bill(
        consumption_kwh=[2500, 0],
        feed_in_kwh=[0.0, 500],
        prices_eur_per_kwh_excl_vat=[0.0851, 0.0641]
    )
