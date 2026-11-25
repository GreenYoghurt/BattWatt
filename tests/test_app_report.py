"""
App-level checks for the "Download volledig rapport (PDF)" button in app.py.

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

# Absolute: newer Streamlit resolves relative AppTest paths against the test file.
APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")
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
    return at


def _report_buttons(at):
    return [b for b in at.get("download_button") if b.proto.label == REPORT_LABEL]


def _start(at):
    next(b for b in at.button if b.label == "🚀 Start Simulatie").click().run()
    assert not at.exception


def test_no_report_button_before_simulation(app):
    assert _report_buttons(app) == []


def test_report_button_after_manual_simulation(app):
    app.radio[1].set_value("Handmatige batterijen").run()
    _start(app)
    assert len(_report_buttons(app)) == 1

    res = app.session_state["simulation_result"]
    ctx = res["report_context"]
    assert ctx["data_source"] == "SlimmeMeterPortal API"
    assert ctx["n_intervals"] == 2 * 96
    assert [p.name for p in ctx["providers"]] == ["Mijn Leverancier"]
    assert report.build_report(res).startswith(b"%PDF-")


def test_report_button_after_sweep_simulation(app):
    assert app.radio[1].value == "Batterijgrootte vergelijking"
    _start(app)
    assert len(_report_buttons(app)) == 1
    sweep = app.session_state["sweep_result"]
    assert report.build_report(sweep, include_fixed=False).startswith(b"%PDF-")
