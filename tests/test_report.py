"""
Tests for report.build_report(): the "Download volledig rapport (PDF)" export.

Results are built with the real Simulator/BillingEngine on two days of
synthetic data, in the same shape app.py stores them in st.session_state.
"""
from datetime import datetime
from io import BytesIO

import numpy as np
import pandas as pd
import pytest
from pypdf import PdfReader

import report
from battery import Battery, get_battery
from billing import BillingEngine, compare_providers, fixed_costs_from_breakdown
from controllers.controller_PV import Controller_PV
from data_checks import CheckResult
from energy_providers import Provider
from models import SimulationResult
from results_tables import SWEEP_EFFICIENCIES, SWEEP_SIZE_PRESETS, SWEEP_SIZES_KWH
from simulator import Simulator

GENERATED_AT = datetime(2026, 11, 25, 14, 30)


@pytest.fixture(scope="module")
def merged_df():
    n = 2 * 96
    rng = pd.date_range("2025-06-06", periods=n, freq="15min")
    phase = np.linspace(0, 4 * np.pi, n)
    df = pd.DataFrame({
        "timestamp": rng,
        "teruglevering": np.maximum(0, np.sin(phase - np.pi / 2)) * 1.2,
        "verbruik": 0.3 + 0.2 * np.maximum(0, np.cos(phase)),
        "day_ahead_price": 0.08 + 0.05 * np.sin(phase),
    })
    return df.set_index("timestamp", drop=False)


@pytest.fixture(scope="module")
def baseline(merged_df):
    df = merged_df.copy()
    net = df["teruglevering"] - df["verbruik"]
    df["adjusted_consumption"] = (-net).clip(lower=0)
    df["adjusted_production"] = net.clip(lower=0)
    return SimulationResult(
        df=df,
        total_production_kwh=merged_df["teruglevering"].sum(),
        total_consumption_kwh=merged_df["verbruik"].sum(),
        total_adjusted_production_kwh=df["adjusted_production"].sum(),
        total_adjusted_consumption_kwh=df["adjusted_consumption"].sum(),
        final_soc_pct=0, final_soc_kwh=0, delta_soc_kwh=0,
    )


DYNAMIC = Provider(name="Dynamo Energie", subscription_cost=75.0, buying_fee=0.02, selling_fee=0.02,
                   net_metering=False, selling_fee_net_metering=True, contract_type="dynamic")
FIXED = Provider(name="Vastberaden BV", subscription_cost=60.0, buying_fee=0.0, selling_fee=0.0,
                 net_metering=False, selling_fee_net_metering=False, contract_type="fixed",
                 normaal_tarief=0.28, dal_tarief=0.26, terugleververgoeding=0.09)


def _simulate(battery, merged_df):
    result = Simulator(battery, Controller_PV(battery)).run(merged_df)
    result.df["battery_soc_kwh"] = result.df["battery_soc"] * battery.capacity_kwh / 100
    return result


def _context(merged_df, providers, checks=()):
    return {
        "data_source": "Bestand upload (automatisch herkend formaat)",
        "period_start": merged_df["timestamp"].min(),
        "period_end": merged_df["timestamp"].max(),
        "n_intervals": len(merged_df),
        "interval_minutes": 15.0,
        "providers": list(providers),
        "acknowledged_checks": [(c.title, c.message) for c in checks],
    }


def _manual_result(merged_df, baseline, providers, checks=()):
    billing = BillingEngine(providers[0])
    batteries = [("Kleine accu", Battery(5.0, 2.5, 2.5, efficiency=0.85, standby_power_w=7.0)),
                 ("Grote accu", get_battery("Bliq_10kwh"))]
    all_results = []
    cost_baseline = billing.calculate_bill(baseline)
    for label, battery in batteries:
        result = _simulate(battery, merged_df)
        cost = billing.calculate_bill(result)
        all_results.append({"label": label, "battery": battery, "result": result, "cost_simulated": cost,
                            "savings": cost_baseline - cost,
                            "breakdown_simulated": billing.calculate_bill_breakdown(result)})
    return {
        "cost_baseline": cost_baseline,
        "breakdown_baseline": billing.calculate_bill_breakdown(baseline),
        "all_results": all_results,
        "strategy": "PV",
        "provider_name": providers[0].name,
        "provider_comparison": (compare_providers(providers, baseline, [r["result"] for r in all_results])
                                if len(providers) > 1 else None),
        "report_context": _context(merged_df, providers, checks),
    }


def _sweep_result(merged_df, baseline, providers):
    billing = BillingEngine(providers[0])
    bd_base = billing.calculate_bill_breakdown(baseline)
    cost_baseline = billing.calculate_bill(baseline)
    cost_baseline_variable = cost_baseline - fixed_costs_from_breakdown(bd_base)
    rows = []
    for size in SWEEP_SIZES_KWH:
        for eff in SWEEP_EFFICIENCIES:
            battery = get_battery(SWEEP_SIZE_PRESETS[size], efficiency=eff)
            result = _simulate(battery, merged_df)
            bd = billing.calculate_bill_breakdown(result)
            cost = billing.calculate_bill(result)
            cost_variable = cost - fixed_costs_from_breakdown(bd)
            rows.append({"size_kwh": size, "efficiency": eff, "battery": battery, "result": result,
                         "cost_total": cost, "cost_variable": cost_variable, "breakdown_simulated": bd,
                         "savings_variable": cost_baseline_variable - cost_variable})
    return {
        "cost_baseline": cost_baseline,
        "cost_baseline_variable": cost_baseline_variable,
        "breakdown_baseline": bd_base,
        "rows": rows,
        "strategy": "PV",
        "provider_name": providers[0].name,
        "provider_comparison": (compare_providers(providers, baseline, [r["result"] for r in rows])
                                if len(providers) > 1 else None),
        "report_context": _context(merged_df, providers),
    }


def _read(pdf_bytes):
    assert pdf_bytes.startswith(b"%PDF-")
    assert pdf_bytes.rstrip().endswith(b"%%EOF")
    reader = PdfReader(BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text() for page in reader.pages)
    images = sum(len(page.images) for page in reader.pages)
    return reader, " ".join(text.split()), images


def test_report_is_branded_jortapp(merged_df, baseline):
    data = _manual_result(merged_df, baseline, [DYNAMIC])
    pdf_bytes = report.build_report(data, generated_at=GENERATED_AT)
    reader, text, _ = _read(pdf_bytes)

    assert reader.metadata.title == "JortApp simulatierapport"
    assert "JortApp simulatierapport" in text
    assert "battwatt" not in text.lower()  # no old product name anywhere visible
    # The docs link is clickable text; the URL (repo still named BattWatt) only
    # lives in the link annotation.
    assert "JortApp-documentatie" in text
    uris = [a.get_object()["/A"].get("/URI") for page in reader.pages
            for a in (page.get("/Annots") or []) if "/A" in a.get_object()]
    assert report.DOCS_URL in uris


def test_manual_single_provider(merged_df, baseline):
    data = _manual_result(merged_df, baseline, [DYNAMIC])
    reader, text, images = _read(report.build_report(data, generated_at=GENERATED_AT))

    assert reader.metadata.title == "JortApp simulatierapport"
    assert "25-11-2026" in text
    # Settings
    assert "06-06-2025 t/m 07-06-2025 (2 dagen)" in text
    assert "Handmatige batterijen" in text
    assert "PV Prioriteit (Zelfconsumptie)" in text
    assert "Kleine accu" in text and "Grote accu" in text
    assert "85%" in text and "Dynamo Energie" in text and "Dynamisch" in text
    assert "€ 75.00/jaar" in text
    assert "Geen waarschuwingen" in text
    # Results
    assert report.eur(data["cost_baseline"]) in text
    for res in data["all_results"]:
        assert report.eur(res["cost_simulated"]) in text
        assert report.eur(res["savings"]) in text
        assert f"{res['result'].total_cycles:.1f}" in text
    assert "Kostenopbouw vergelijking" in text and "Energiebelasting" in text
    assert "Vergelijking energieleveranciers" not in text
    assert "schattingen" in text
    # One usage and one price chart per battery for a two-day run
    assert images == 4


def test_manual_several_providers_and_acknowledged_warning(merged_df, baseline):
    check = CheckResult(passed=False, severity="warning", title="Mogelijk bestandscorruptie gedetecteerd",
                        message="3 van 192 intervallen (1.6%) hebben een te hoge verbruikswaarde.")
    data = _manual_result(merged_df, baseline, [DYNAMIC, FIXED], checks=[check])
    _, text, images = _read(report.build_report(data, generated_at=GENERATED_AT))

    assert "Vergelijking energieleveranciers" in text
    assert "Vastberaden BV" in text and "Vast" in text
    assert "normaal € 0.2800/kWh" in text
    assert "Laagste jaarkosten" in text
    fixed_entry = data["provider_comparison"][1]
    # Comparison table cells are plain numbers, as in the app's table/CSV.
    assert f"{fixed_entry['cost_baseline']:,.2f}" in text
    assert "Mogelijk bestandscorruptie gedetecteerd" in text
    assert images == 4 + 2  # plus cost and savings per provider


def test_manual_without_fixed_costs(merged_df, baseline):
    data = _manual_result(merged_df, baseline, [DYNAMIC])
    _, text, _ = _read(report.build_report(data, include_fixed=False, generated_at=GENERATED_AT))
    variable = data["cost_baseline"] - fixed_costs_from_breakdown(data["breakdown_baseline"])
    assert report.eur(variable) in text
    assert "Abonnementskosten" not in text


def test_sweep_single_provider(merged_df, baseline):
    data = _sweep_result(merged_df, baseline, [DYNAMIC])
    _, text, images = _read(report.build_report(data, generated_at=GENERATED_AT))

    assert "Batterijgrootte vergelijking" in text
    assert "12 simulaties" in text
    assert report.eur(data["cost_baseline"]) in text
    assert "Details per configuratie" in text
    best = max(data["rows"], key=lambda r: r["savings_variable"])
    assert report.eur(best["savings_variable"]) in text
    assert "Vergelijking energieleveranciers" not in text
    assert images == 2


def test_sweep_several_providers(merged_df, baseline):
    data = _sweep_result(merged_df, baseline, [DYNAMIC, FIXED])
    _, text, images = _read(report.build_report(data, generated_at=GENERATED_AT))

    assert "Vergelijking energieleveranciers" in text
    assert "Vastberaden BV" in text
    assert images == 4


def test_long_run_includes_seasonal_weeks():
    n = 120 * 96  # 120 days from January: winter and spring weeks
    rng = pd.date_range("2025-01-01", periods=n, freq="15min")
    df = pd.DataFrame({"timestamp": rng, "battery_soc": 50.0})
    titles = [t for t, _ in report.chart_slices(df)]
    assert titles == ["Volledige periode", "Winter (jan)", "Lente (apr)"]


def test_report_has_no_meter_values_or_file_names(merged_df, baseline):
    data = _manual_result(merged_df, baseline, [DYNAMIC])
    _, text, _ = _read(report.build_report(data, generated_at=GENERATED_AT))
    assert ".csv" not in text and ".xlsx" not in text
    # The first raw consumption value must not appear as a number in the report.
    first = f"{merged_df['verbruik'].iloc[1]:.4f}"
    assert first not in text


def test_duplicate_battery_labels_keep_their_own_column(merged_df, baseline):
    """Two batteries named alike used to collapse into one Kostenopbouw column."""
    from results_tables import breakdown_dataframe
    data = _manual_result(merged_df, baseline, [DYNAMIC])
    for res in data["all_results"]:
        res["label"] = "10 kWh"
    costs = [r["cost_simulated"] for r in data["all_results"]]
    df = breakdown_dataframe(data["all_results"], data["breakdown_baseline"], data["cost_baseline"], costs, True)
    assert ["10 kWh (€)", "10 kWh (2) (€)"] == list(df.columns[-2:])
    assert list(df.iloc[-1, -2:]) == [round(c, 2) for c in costs]
