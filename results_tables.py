"""Plain-data views of simulation results, shared by app.py and report.py.

Everything here works on the result dicts app.py stores in
``st.session_state`` (``simulation_result`` / ``sweep_result``) and on the
entries of ``billing.compare_providers()``. No Streamlit and no plotting, so
the PDF report can build the same tables the page shows.
"""
import pandas as pd

# Automatic battery-size x efficiency sweep ("Batterijgrootte vergelijking" mode)
SWEEP_SIZES_KWH = [5, 10, 15, 20]
SWEEP_SIZE_PRESETS = {5: "Bliq_5kwh", 10: "Bliq_10kwh", 15: "Bliq_15kwh", 20: "Bliq_20kwh"}
SWEEP_EFFICIENCIES = [0.80, 0.90, 0.95]


def breakdown_row_defs(bd):
    """Returns (label, key, is_credit, vol_key, is_fixed, tarief_str) row tuples for the
    Kostenopbouw tables, tailored to the provider's contract type."""
    def avg_per_kwh(cost_key, vol_key):
        vol = bd[vol_key]
        return bd[cost_key] / vol if vol else 0.0

    fixed_rows = [
        ("Abonnementskosten",    "abonnementskosten",    False, None, True,
         f"€ {bd['tarief_abonnementskosten']:.2f}/jaar"),
        ("Netbeheerskosten",     "netbeheerskosten",      False, None, True,
         f"€ {bd['tarief_netbeheerskosten']:.2f}/jaar"),
        ("Belastingvermindering", "belastingvermindering", True,  None, True,
         f"€ {bd['tarief_belastingvermindering']:.2f}/jaar"),
    ]

    if bd.get("contract_type") == "fixed":
        variable_rows = [
            ("Normaaltarief",        "normaal_kosten",          False, "normaal_kwh",        False,
             f"€ {bd['tarief_normaal_tarief']:.4f}/kWh"),
            ("Daltarief",            "dal_kosten",              False, "dal_kwh",            False,
             f"€ {bd['tarief_dal_tarief']:.4f}/kWh"),
            ("Terugleververgoeding", "teruglevering_opbrengst", True,  "total_feed_in_kwh",  False,
             f"€ {bd['tarief_terugleververgoeding']:.4f}/kWh"),
        ]
    else:
        variable_rows = [
            ("Marktprijs inkoop",                "marktprijs_inkoop",          False, "total_consumption_kwh", False,
             f"gem. € {avg_per_kwh('marktprijs_inkoop', 'total_consumption_kwh'):.4f}/kWh"),
            ("Energiebelasting",                 "energiebelasting",           False, "energiebelasting_kwh",  False,
             f"€ {bd['tarief_energiebelasting_per_kwh']:.4f}/kWh"),
            ("Leveranciersopslag inkoop",        "leveranciersopslag_inkoop",  False, "total_consumption_kwh", False,
             f"€ {bd['tarief_leveranciersopslag_inkoop_per_kwh']:.4f}/kWh"),
            ("Leveranciersopslag teruglevering", "leveranciersopslag_verkoop", False, "total_feed_in_kwh",     False,
             f"€ {bd['tarief_leveranciersopslag_verkoop_per_kwh']:.4f}/kWh"),
            ("Teruglevering opbrengst",          "teruglevering_opbrengst",    True,  "total_feed_in_kwh",     False,
             f"gem. € {avg_per_kwh('teruglevering_opbrengst', 'total_feed_in_kwh'):.4f}/kWh"),
        ]

    return fixed_rows + variable_rows


def breakdown_dataframe(all_results, breakdown_baseline, display_baseline, display_costs, include_fixed):
    """Plain-table version of the unified Kostenopbouw, for copy/export."""
    rows = [r for r in breakdown_row_defs(breakdown_baseline) if include_fixed or not r[4]]
    # Unique column names: two batteries with the same label must not collapse into one column.
    labels = unique_labels([res['label'] for res in all_results])
    out = []
    for label, key, is_credit, _vol, _fixed, tarief in rows:
        sign = -1 if is_credit else 1
        row = {"Post": label, "Tarief": tarief,
               "Zonder batterij (€)": round(sign * breakdown_baseline[key], 2)}
        for lbl, res in zip(labels, all_results):
            row[f"{lbl} (€)"] = round(sign * res['breakdown_simulated'][key], 2)
        out.append(row)
    total = {"Post": "Totaal", "Tarief": "", "Zonder batterij (€)": round(display_baseline, 2)}
    for lbl, dc in zip(labels, display_costs):
        total[f"{lbl} (€)"] = round(dc, 2)
    out.append(total)
    return pd.DataFrame(out)


def sweep_summary_dataframe(rows, include_fixed):
    """The 'Details per configuratie' table of the battery-size sweep."""
    cost_key = 'cost_total' if include_fixed else 'cost_variable'
    cost_col = f"Totale kosten {'incl.' if include_fixed else 'excl.'} vaste kosten (€)"
    return pd.DataFrame([{
        "Batterijgrootte (kWh)": r['size_kwh'],
        "Rendement": f"{r['efficiency']:.0%}",
        cost_col: round(r[cost_key], 2),
        "Besparing (€)": round(r['savings_variable'], 2),
        "Batterij cycli": round(getattr(r['result'], 'total_cycles', 0.0), 1),
    } for r in rows])


def unique_labels(labels):
    """Appends ' (2)', ' (3)', ... to repeated labels so they can serve as keys."""
    seen, out = {}, []
    for lbl in labels:
        seen[lbl] = seen.get(lbl, 0) + 1
        out.append(f"{lbl} ({seen[lbl]})" if seen[lbl] > 1 else lbl)
    return out


def contract_label(contract_type):
    return "Vast" if contract_type == "fixed" else "Dynamisch"


def provider_cost(entry, include_fixed, baseline=False):
    """Cost of a compare_providers() entry (baseline=True) or result row."""
    if baseline:
        return entry['cost_baseline'] if include_fixed else entry['cost_baseline_variable']
    return entry['cost_total'] if include_fixed else entry['cost_variable']


def provider_comparison_dataframe(comparison, battery_labels, include_fixed):
    """One row per provider: yearly cost without and with each battery, then the
    savings per battery (savings don't depend on include_fixed)."""
    names = unique_labels([c['name'] for c in comparison])
    out = []
    for name, c in zip(names, comparison):
        row = {"Leverancier": name, "Contract": contract_label(c['contract_type']),
               "Zonder batterij (€)": round(provider_cost(c, include_fixed, baseline=True), 2)}
        for lbl, r in zip(battery_labels, c['results']):
            row[f"{lbl} (€)"] = round(provider_cost(r, include_fixed), 2)
        for lbl, r in zip(battery_labels, c['results']):
            row[f"Besparing {lbl} (€)"] = round(r['savings'], 2)
        out.append(row)
    return pd.DataFrame(out)


def sweep_provider_dataframe(comparison, rows, include_fixed):
    """Long-format table: one line per provider × battery configuration, with a
    'geen batterij' line (0 kWh) per provider first."""
    names = unique_labels([c['name'] for c in comparison])
    cost_col = f"Totale kosten {'incl.' if include_fixed else 'excl.'} vaste kosten (€)"
    out = []
    for name, c in zip(names, comparison):
        contract = contract_label(c['contract_type'])
        out.append({"Leverancier": name, "Contract": contract,
                    "Batterijgrootte (kWh)": 0, "Rendement": "—",
                    cost_col: round(provider_cost(c, include_fixed, baseline=True), 2),
                    "Besparing (€)": 0.0})
        for sweep_row, r in zip(rows, c['results']):
            out.append({"Leverancier": name, "Contract": contract,
                        "Batterijgrootte (kWh)": sweep_row['size_kwh'],
                        "Rendement": f"{sweep_row['efficiency']:.0%}",
                        cost_col: round(provider_cost(r, include_fixed), 2),
                        "Besparing (€)": round(r['savings'], 2)})
    return pd.DataFrame(out)


def cheapest_option(comparison, option_labels, option_costs):
    """(cost, provider name, option label) of the cheapest provider × option
    combination. option_costs(entry) returns one cost per label in option_labels."""
    names = unique_labels([c['name'] for c in comparison])
    return min(((cost, name, lbl)
                for name, c in zip(names, comparison)
                for lbl, cost in zip(option_labels, option_costs(c))),
               key=lambda t: t[0])


def all_option_costs(include_fixed):
    return lambda c: ([provider_cost(c, include_fixed, baseline=True)]
                      + [provider_cost(r, include_fixed) for r in c['results']])
