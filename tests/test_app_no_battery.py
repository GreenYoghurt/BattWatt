"""
App-level tests for the 'Geen batterij (alleen huidige kosten)' mode of app.py:
the current situation is billed without simulating any battery.

Meter data is injected via the SlimmeMeterPortal session-state path and the
ENTSO-E fetch is replaced by synthetic prices, so no network is needed.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import data_loader
import report
from billing import BillingEngine, compare_providers, fixed_costs_from_breakdown
from energy_providers import Provider
from models import SimulationResult
from results_tables import NO_BATTERY_MODE

# Absolute: newer Streamlit resolves relative AppTest paths against the test file.
APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")
START_LABEL = "🚀 Bereken Kosten"
REPORT_LABEL = "📄 Download volledig rapport (PDF)"


def _meter_df():
    n = 2 * 96
    rng = pd.date_range("2025-06-06", periods=n, freq="15min")
    phase = np.linspace(0, 4 * np.pi, n)
    return pd.DataFrame({
        "timestamp": rng,
        "verbruik": 0.3 + 0.2 * np.maximum(0, np.cos(phase)),
        "teruglevering": np.maximum(0, np.sin(phase - np.pi / 2)) * 1.2,
    })


def _fake_prices(api_key, start_date, end_date, country_code="NL"):
    rng = pd.date_range(start_date.floor("D"), end_date.ceil("D"), freq="15min")
    phase = np.linspace(0, 4 * np.pi, len(rng))
    return pd.DataFrame({"timestamp": rng, "day_ahead_price": 80 + 50 * np.sin(phase)})  # EUR/MWh


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(data_loader, "fetch_entsoe_prices", _fake_prices)
    at = AppTest.from_file(APP_PATH, default_timeout=180)
    at.secrets["ENTSOE_API_KEY"] = "test"
    at.session_state["smp_meter_df"] = _meter_df()
    at.run()
    at.radio[0].set_value("SlimmeMeterPortal API").run()
    at.radio[1].set_value(NO_BATTERY_MODE).run()
    return at


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def _start(at):
    _button(at, START_LABEL).click().run()
    assert not at.exception


def _metric(at, label):
    return next(m.value for m in at.metric if m.label == label)


def _expected_baseline():
    """The baseline the app should bill: meter data merged with the fake
    prices (EUR/kWh), flows netted per interval, exactly as app.py does."""
    meter = _meter_df()
    prices = _fake_prices(None, meter["timestamp"].min(), meter["timestamp"].max())
    merged = data_loader.merge_data(meter, prices)
    merged["day_ahead_price"] = merged["day_ahead_price"] / 1000
    merged = merged.set_index("timestamp", drop=False)
    net = merged["teruglevering"] - merged["verbruik"]
    merged["adjusted_consumption"] = (-net).clip(lower=0)
    merged["adjusted_production"] = net.clip(lower=0)
    return SimulationResult(df=merged, total_production_kwh=0, total_consumption_kwh=0,
                            total_adjusted_production_kwh=0, total_adjusted_consumption_kwh=0,
                            final_soc_pct=0, final_soc_kwh=0, delta_soc_kwh=0)


def test_no_battery_mode_hides_battery_settings(app):
    assert app.radio[1].value == NO_BATTERY_MODE
    # No battery editors, no strategy selection.
    assert not any(b.label == "➕ Batterij toevoegen" for b in app.button)
    assert not any(s.label == "Selecteer Strategie" for s in app.selectbox)
    assert "🎛️ Aansturing" not in [s.value for s in app.sidebar.subheader]
    assert not _button(app, START_LABEL).disabled


def test_no_battery_single_provider(app):
    _start(app)
    res = app.session_state["simulation_result"]
    assert res["all_results"] == []
    assert res["strategy"] is None
    assert res["provider_comparison"] is None
    assert "sweep_result" not in app.session_state

    # Billed exactly like the baseline of the battery modes.
    provider = Provider(name="Mijn Leverancier", subscription_cost=75.0, buying_fee=0.02, selling_fee=0.02,
                        net_metering=False, selling_fee_net_metering=True, contract_type="dynamic")
    expected = BillingEngine(provider).calculate_bill(_expected_baseline())
    assert res["cost_baseline"] == pytest.approx(expected)

    assert "Resultaten Overzicht: huidige situatie" in [h.value for h in app.header]
    assert _metric(app, "Jaarkosten (huidige situatie)") == f"€{expected:,.2f}"
    labels = [m.label for m in app.metric]
    assert not any("Besparing" in lbl or "Cycli" in lbl for lbl in labels)
    assert "Vergelijking energieleveranciers" not in [s.value for s in app.subheader]

    # Kostenopbouw table: one amount column, totals match the bill.
    table = next(df.value for df in app.dataframe if "Post" in df.value.columns)
    assert list(table.columns) == ["Post", "Tarief", "Zonder batterij (€)"]
    assert table.iloc[-1]["Zonder batterij (€)"] == pytest.approx(round(expected, 2))
    assert "Energiebelasting" in list(table["Post"])

    # Report button is there and the PDF builds.
    assert any(b.proto.label == REPORT_LABEL for b in app.get("download_button"))
    assert report.build_report(res).startswith(b"%PDF-")


def test_no_battery_without_fixed_costs(app):
    _start(app)
    res = app.session_state["simulation_result"]
    app.checkbox(key="baseline_include_fixed").uncheck().run()
    assert not app.exception
    variable = res["cost_baseline"] - fixed_costs_from_breakdown(res["breakdown_baseline"])
    assert _metric(app, "Jaarkosten (huidige situatie)") == f"€{variable:,.2f}"
    table = next(df.value for df in app.dataframe if "Post" in df.value.columns)
    assert "Abonnementskosten" not in list(table["Post"])


def test_no_battery_two_providers_with_fixed_contract(app):
    _button(app, "➕ Leverancier toevoegen").click().run()
    app.radio(key="prov_type_1").set_value("Vast contract").run()
    # The incl./excl. energy tax choice is still required without a battery.
    assert _button(app, START_LABEL).disabled
    app.radio(key="prov_eb_1").set_value("Inclusief energiebelasting").run()
    _start(app)

    res = app.session_state["simulation_result"]
    comparison = res["provider_comparison"]
    assert [c["contract_type"] for c in comparison] == ["dynamic", "fixed"]
    assert all(c["results"] == [] for c in comparison)
    assert comparison[0]["cost_baseline"] == pytest.approx(res["cost_baseline"])

    fixed = Provider(name="Leverancier 2", subscription_cost=75.0, buying_fee=0.0, selling_fee=0.0,
                     net_metering=False, selling_fee_net_metering=False, contract_type="fixed",
                     normaal_tarief=0.28, dal_tarief=0.26, terugleververgoeding=0.09)
    expected_fixed = BillingEngine(fixed).calculate_bill(_expected_baseline())
    assert comparison[1]["cost_baseline"] == pytest.approx(expected_fixed)

    assert "Vergelijking energieleveranciers" in [s.value for s in app.subheader]
    table = next(df.value for df in app.dataframe if "Leverancier" in df.value.columns)
    assert list(table["Contract"]) == ["Dynamisch", "Vast"]
    assert not any(c.startswith("Besparing") for c in table.columns)
    assert table["Zonder batterij (€)"].tolist() == pytest.approx(
        [round(c["cost_baseline"], 2) for c in comparison])

    pdf = report.build_report(res, include_fixed=False)
    assert pdf.startswith(b"%PDF-")


def test_switching_back_to_batteries_still_works(app):
    _start(app)
    app.radio[1].set_value("Handmatige batterijen").run()
    assert not app.exception
    # The stored no-battery result is still shown until a new run.
    assert "Resultaten Overzicht: huidige situatie" in [h.value for h in app.header]
    _button(app, "🚀 Start Simulatie").click().run()
    assert not app.exception
    assert len(app.session_state["simulation_result"]["all_results"]) == 1
    assert "Resultaten Overzicht" in [h.value for h in app.header]


# ── Season-week tabs for long periods ────────────────────────────────────────

SEASON_TABS = ["📊 Volledige Periode", "❄️ Winter (Jan)", "🌱 Lente (Apr)", "☀️ Zomer (Jul)", "🍂 Herfst (Okt)"]


def _long_meter_df(start, days):
    n = days * 96
    rng = pd.date_range(start, periods=n, freq="15min")
    phase = np.linspace(0, days * 2 * np.pi, n)  # one day/night cycle per day
    return pd.DataFrame({
        "timestamp": rng,
        "verbruik": 0.3 + 0.2 * np.maximum(0, np.cos(phase)),
        "teruglevering": np.maximum(0, np.sin(phase - np.pi / 2)) * 1.2,
    })


def _app_with(monkeypatch, meter_df, mode):
    monkeypatch.setattr(data_loader, "fetch_entsoe_prices", _fake_prices)
    at = AppTest.from_file(APP_PATH, default_timeout=300)
    at.secrets["ENTSOE_API_KEY"] = "test"
    at.session_state["smp_meter_df"] = meter_df
    at.run()
    at.radio[0].set_value("SlimmeMeterPortal API").run()
    at.radio[1].set_value(mode).run()
    return at


def test_no_battery_full_year_has_season_tabs(monkeypatch):
    # A full year in 'Geen batterij' mode gets the same season weeks as the
    # battery modes, with only the usage chart (no battery/price chart).
    at = _app_with(monkeypatch, _long_meter_df("2025-01-01", 365), NO_BATTERY_MODE)
    _start(at)
    assert [t.label for t in at.tabs] == SEASON_TABS
    for tab in at.tabs:
        charts = tab.get("plotly_chart")
        assert len(charts) == 1
    assert "Huisverbruik & Zon - Volledige Periode" in str(at.tabs[0].get("plotly_chart")[0].proto.spec)


def test_no_battery_short_period_has_no_tabs(app):
    _start(app)  # 2 days of data
    assert not any(t.label in SEASON_TABS for t in app.tabs)


def test_battery_mode_long_period_keeps_usage_and_price_charts(monkeypatch):
    # 40 days spanning April: battery modes still show both charts per tab.
    at = _app_with(monkeypatch, _long_meter_df("2025-03-20", 40), "Handmatige batterijen")
    _button(at, "🚀 Start Simulatie").click().run()
    assert not at.exception
    assert [t.label for t in at.tabs][:2] == ["📊 Volledige Periode", "🌱 Lente (Apr)"]
    for tab in at.tabs[:2]:
        assert len(tab.get("plotly_chart")) == 2
