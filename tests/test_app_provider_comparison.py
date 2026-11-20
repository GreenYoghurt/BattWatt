"""
App-level smoke tests for comparing multiple energy providers in app.py.

Meter data is injected via the SlimmeMeterPortal session-state path and the
ENTSO-E fetch is replaced by synthetic prices, so no network is needed.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import data_loader

# Absolute: newer Streamlit resolves relative AppTest paths against the test file.
APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


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
    return at


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def _add_fixed_provider(at):
    _button(at, "➕ Leverancier toevoegen").click().run()
    assert at.session_state["provider_ids"] == [0, 1]
    at.radio(key="prov_type_1").set_value("Vast contract").run()


def _subheaders(at):
    return [s.value for s in at.subheader]


def test_single_provider_has_no_comparison_section(app):
    app.radio[1].set_value("Handmatige batterijen").run()
    _button(app, "🚀 Start Simulatie").click().run()
    assert not app.exception
    assert "Vergelijking energieleveranciers" not in _subheaders(app)
    assert app.session_state["simulation_result"]["provider_comparison"] is None


def test_manual_batteries_with_two_providers(app):
    app.radio[1].set_value("Handmatige batterijen").run()
    _add_fixed_provider(app)
    _button(app, "🚀 Start Simulatie").click().run()
    assert not app.exception

    res = app.session_state["simulation_result"]
    comparison = res["provider_comparison"]
    assert [c["contract_type"] for c in comparison] == ["dynamic", "fixed"]
    # The first provider's numbers are the ones shown in the existing overview.
    assert comparison[0]["cost_baseline"] == pytest.approx(res["cost_baseline"])
    assert comparison[0]["results"][0]["cost_total"] == pytest.approx(res["all_results"][0]["cost_simulated"])
    assert "Vergelijking energieleveranciers" in _subheaders(app)

    table = next(df.value for df in app.dataframe if "Leverancier" in df.value.columns)
    assert list(table["Contract"]) == ["Dynamisch", "Vast"]


def test_removing_a_provider(app):
    _add_fixed_provider(app)
    app.button(key="del_prov_1").click().run()
    assert app.session_state["provider_ids"] == [0]
    assert not app.exception
    assert app.text_input(key="prov_name_0").value == "Mijn Leverancier"


def test_sweep_with_two_providers(app):
    assert app.radio[1].value == "Batterijgrootte vergelijking"
    _add_fixed_provider(app)
    _button(app, "🚀 Start Simulatie").click().run()
    assert not app.exception

    sweep = app.session_state["sweep_result"]
    comparison = sweep["provider_comparison"]
    assert len(comparison) == 2
    assert all(len(c["results"]) == len(sweep["rows"]) for c in comparison)
    assert comparison[0]["cost_baseline"] == pytest.approx(sweep["cost_baseline"])
    assert "Vergelijking energieleveranciers" in _subheaders(app)
