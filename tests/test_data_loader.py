import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from unittest.mock import patch
from data_loader import (
    SmartLoader, SlimmeMeterPortalAPILoader, fetch_entsoe_prices,
    EntsoeFetchError, ENTSOE_RETRY_DELAYS,
)
from entsoe.exceptions import NoMatchingDataError
import requests
import json
import io


def test_fetch_entsoe_prices_redacts_security_token():
    leaked_url = (
        "400 Client Error: for url: https://web-api.tp.entsoe.eu/api"
        "?documentType=A44&securityToken=9b9a67b5-c628-4d61-9fae-603b65e62b9e"
        "&periodStart=202412302300&periodEnd=202512302300"
    )
    with patch("data_loader.EntsoePandasClient") as mock_client_cls:
        mock_client_cls.return_value.query_day_ahead_prices.side_effect = RuntimeError(leaked_url)
        with pytest.raises(RuntimeError) as exc_info:
            fetch_entsoe_prices(
                "9b9a67b5-c628-4d61-9fae-603b65e62b9e",
                pd.Timestamp("2025-01-01"),
                pd.Timestamp("2025-01-02"),
            )

    message = str(exc_info.value)
    assert "9b9a67b5-c628-4d61-9fae-603b65e62b9e" not in message
    assert "securityToken=***REDACTED***" in message


def _http_error(status_code):
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(f"{status_code} Error for url: ...", response=response)


@pytest.mark.parametrize("error, expected_kind", [
    (_http_error(503), "unavailable"),
    (_http_error(401), "auth"),
    (_http_error(429), "rate_limit"),
    (_http_error(400), "bad_request"),
    (requests.ReadTimeout("timed out"), "timeout"),
    (requests.ConnectionError("no route"), "connection"),
    (NoMatchingDataError(), "no_data"),
    (ValueError("something else"), "unknown"),
])
def test_fetch_entsoe_prices_classifies_errors(error, expected_kind):
    with patch("data_loader.EntsoePandasClient") as mock_client_cls, \
            patch("data_loader.time.sleep"):
        mock_client_cls.return_value.query_day_ahead_prices.side_effect = error
        with pytest.raises(EntsoeFetchError) as exc_info:
            fetch_entsoe_prices("dummy-key", pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-02"))

    assert exc_info.value.kind == expected_kind
    # An empty str(exc) (e.g. bare NoMatchingDataError) must not yield a bare prefix.
    assert str(exc_info.value).strip().endswith(":") is False


def test_fetch_entsoe_prices_retries_transient_errors():
    with patch("data_loader.EntsoePandasClient") as mock_client_cls, \
            patch("data_loader.time.sleep") as mock_sleep:
        query = mock_client_cls.return_value.query_day_ahead_prices
        query.side_effect = _http_error(503)
        with pytest.raises(EntsoeFetchError):
            fetch_entsoe_prices("dummy-key", pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-02"))

    assert query.call_count == len(ENTSOE_RETRY_DELAYS) + 1
    assert [c.args[0] for c in mock_sleep.call_args_list] == list(ENTSOE_RETRY_DELAYS)


def test_fetch_entsoe_prices_does_not_retry_auth_errors():
    with patch("data_loader.EntsoePandasClient") as mock_client_cls, \
            patch("data_loader.time.sleep"):
        query = mock_client_cls.return_value.query_day_ahead_prices
        query.side_effect = _http_error(401)
        with pytest.raises(EntsoeFetchError):
            fetch_entsoe_prices("dummy-key", pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-02"))

    assert query.call_count == 1


def test_fetch_entsoe_prices_succeeds_after_transient_error():
    prices = pd.Series(
        [100.0, 110.0],
        index=pd.DatetimeIndex(["2025-01-01 00:00", "2025-01-01 01:00"], tz="Europe/Amsterdam"),
    )
    with patch("data_loader.EntsoePandasClient") as mock_client_cls, \
            patch("data_loader.time.sleep"):
        mock_client_cls.return_value.query_day_ahead_prices.side_effect = [_http_error(503), prices]
        df = fetch_entsoe_prices("dummy-key", pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-02"))

    assert list(df.columns) == ["timestamp", "day_ahead_price"]
    assert df["day_ahead_price"].tolist() == [100.0, 110.0]
    assert df["timestamp"].dt.tz is None

def test_homewizard_auto_detect(tmp_path):
    # Create a dummy HomeWizard CSV
    csv_path = tmp_path / "homewizard.csv"
    data = {
        "time": ["2025-01-01 00:00", "2025-01-01 00:15", "2025-01-01 00:30"],
        "Import T1 kWh": [100.0, 100.5, 101.2],
        "Import T2 kWh": [50.0, 50.2, 50.5],
        "Export T1 kWh": [10.0, 10.1, 10.3],
        "Export T2 kWh": [5.0, 5.0, 5.1],
    }
    pd.DataFrame(data).to_csv(csv_path, index=False)
    
    df = SmartLoader.load(csv_path)
    
    assert len(df) == 3 # First row is kept (0 values)
    assert "verbruik" in df.columns
    assert "teruglevering" in df.columns
    assert df.iloc[1]["verbruik"] == pytest.approx(0.7)  # (100.5-100) + (50.2-50)

def test_generic_mapped_loader_with_json(tmp_path):
    # Create a custom CSV
    csv_path = tmp_path / "custom.csv"
    data = {
        "Tijdstip": ["2025-01-01 00:00", "2025-01-01 00:15"],
        "In": [1.0, 1.2],
        "Uit": [0.5, 0.6],
    }
    pd.DataFrame(data).to_csv(csv_path, index=False, sep=";")
    
    config = {
        "format": "csv",
        "delimiter": ";",
        "columns": {
            "timestamp": "Tijdstip",
            "import": "In",
            "export": "Uit"
        },
        "is_cumulative": False
    }
    
    config_path = tmp_path / "config.json"
    with open(config_path, "w") as f:
        json.dump(config, f)
    
    df = SmartLoader.load(csv_path, config=config_path)

    assert len(df) == 2
    assert df.iloc[0]["verbruik"] == 1.0
    assert df.iloc[1]["teruglevering"] == 0.6

def test_generic_mapped_loader_signed_column(tmp_path):
    csv_path = tmp_path / "signed.csv"
    data = {
        "Tijdstip": ["2025-01-01 00:00", "2025-01-01 00:15", "2025-01-01 00:30"],
        "Netto": [0.5, -0.3, 0.0],
    }
    pd.DataFrame(data).to_csv(csv_path, index=False, sep=";")

    config = {
        "format": "csv",
        "delimiter": ";",
        "columns": {
            "timestamp": "Tijdstip",
            "value": "Netto"
        },
        "is_cumulative": False
    }

    df = SmartLoader.load(csv_path, config=config)

    assert len(df) == 3
    assert df.iloc[0]["verbruik"] == pytest.approx(0.5)
    assert df.iloc[0]["teruglevering"] == pytest.approx(0.0)
    assert df.iloc[1]["verbruik"] == pytest.approx(0.0)
    assert df.iloc[1]["teruglevering"] == pytest.approx(0.3)

def test_generic_mapped_loader_signed_column_cumulative(tmp_path):
    csv_path = tmp_path / "signed_cumulative.csv"
    data = {
        "Tijdstip": pd.date_range("2025-01-01", periods=4, freq="15min").astype(str),
        "Netto": [10.0, 10.5, 10.2, 11.4],  # cumulative signed meter reading
    }
    pd.DataFrame(data).to_csv(csv_path, index=False, sep=";")

    config = {
        "format": "csv",
        "delimiter": ";",
        "columns": {
            "timestamp": "Tijdstip",
            "value": "Netto"
        },
        "is_cumulative": True
    }

    df = SmartLoader.load(csv_path, config=config)

    assert len(df) == 3
    assert df.iloc[0]["verbruik"] == pytest.approx(0.5)
    assert df.iloc[1]["teruglevering"] == pytest.approx(0.3)
    assert df.iloc[2]["verbruik"] == pytest.approx(1.2)

def test_file_like_object_support():
    # Simulate a Streamlit UploadedFile using BytesIO
    content = (
        "time,Import T1 kWh,Import T2 kWh,Export T1 kWh,Export T2 kWh\n"
        "2025-01-01 00:00,100,50,10,5\n"
        "2025-01-01 00:15,100.5,50.2,10.1,5.0\n"
    )
    # We use BytesIO because read_csv/read_excel often handle bytes in buffers
    file_like = io.BytesIO(content.encode('utf-8'))
    # Attach a name so the auto-detector knows it's a CSV
    file_like.name = "uploaded_data.csv"
    
    df = SmartLoader.load(file_like)
    
    assert len(df) == 2
    assert df.iloc[1]["verbruik"] == pytest.approx(0.7)
    # Check that seek(0) works and we can read it again or that it was read correctly
    assert file_like.tell() > 0

def test_gap_detection(tmp_path, capsys):
    # Create data with a gap
    csv_path = tmp_path / "gap.csv"
    data = {
        "time": [
            "2025-01-01 00:00", 
            "2025-01-01 00:15", 
            "2025-01-01 00:30", 
            "2025-01-01 00:45",
            "2025-01-01 02:00" # Gap here (1h 15m instead of 15m)
        ],
        "Import T1 kWh": [100.0, 100.1, 100.2, 100.3, 100.4],
        "Import T2 kWh": [0, 0, 0, 0, 0],
        "Export T1 kWh": [0, 0, 0, 0, 0],
        "Export T2 kWh": [0, 0, 0, 0, 0],
    }
    pd.DataFrame(data).to_csv(csv_path, index=False)
    
    SmartLoader.load(csv_path)
    captured = capsys.readouterr()
    assert "Warning: Detected 1 gaps" in captured.out

def test_negative_value_clip_warning(tmp_path, capsys):
    # A custom mapping where the source column is signed (e.g. a mistakenly
    # unhandled negative-feed-in export) should warn, not silently drop data.
    csv_path = tmp_path / "negative.csv"
    data = {
        "Tijdstip": ["2025-01-01 00:00", "2025-01-01 00:15"],
        "In": [1.0, 1.2],
        "Uit": [0.5, -0.6],
    }
    pd.DataFrame(data).to_csv(csv_path, index=False, sep=";")

    config = {
        "format": "csv",
        "delimiter": ";",
        "columns": {
            "timestamp": "Tijdstip",
            "import": "In",
            "export": "Uit"
        },
        "is_cumulative": False
    }

    df = SmartLoader.load(csv_path, config=config)
    captured = capsys.readouterr()

    assert "Warning: Clipping negative values to 0" in captured.out
    assert df.iloc[1]["teruglevering"] == 0.0

def test_slimme_meter_portal_auto_detect():
    df = SmartLoader.load(Path(__file__).parent / "data" / "SlimmeMeterPortal.xlsx")

    assert len(df) == 96
    assert "verbruik" in df.columns
    assert "teruglevering" in df.columns
    assert df["verbruik"].sum() == pytest.approx(6.265)
    # The export stores feed-in as negative values; the loader must take the
    # absolute value rather than let them get clipped to 0 as "invalid".
    assert df["teruglevering"].sum() == pytest.approx(8.384)
    assert (df["teruglevering"] > 0).sum() == 43

def test_slimme_meter_portal_negative_feedin(tmp_path):
    # SlimmeMeterPortal.nl stores feed-in (teruglevering) as negative values.
    xlsx_path = tmp_path / "slimme_meter.xlsx"
    data = {
        "Tijdstip": pd.date_range("2025-01-01", periods=3, freq="15min"),
        "levering normaaltarief [kWh]": [0.1, 0.0, 0.2],
        "teruglevering normaaltarief [kWh]": [0.0, -0.3, -0.05],
        "levering laagtarief [kWh]": [0.0, 0.0, 0.0],
        "teruglevering laagtarief [kWh]": [0.0, 0.0, 0.0],
    }
    pd.DataFrame(data).to_excel(xlsx_path, index=False)

    df = SmartLoader.load(xlsx_path)

    assert df["verbruik"].tolist() == pytest.approx([0.1, 0.0, 0.2])
    assert df["teruglevering"].tolist() == pytest.approx([0.0, 0.3, 0.05])

def test_slimme_meter_portal_api_loader_high_low():
    # Real API format: "dd-mm-YYYY HH:MM:SS +ZZZZ" timestamps, Dutch
    # decimal-comma numbers, and `None` for unpopulated tariff fields.
    usages = [
        {
            "time": "01-01-2025 00:00:00 +0100",
            "delivery_high": "0,10",
            "delivery_low": "0,05",
            "returned_delivery_high": None,
            "returned_delivery_low": "0,20",
            "temperature": "5,0",
        },
        {
            "time": "01-01-2025 00:15:00 +0100",
            "delivery_high": "0,20",
            "delivery_low": None,
            "returned_delivery_high": None,
            "returned_delivery_low": "0,00",
            "temperature": "5,1",
        },
    ]

    df = SlimmeMeterPortalAPILoader().load_usages(usages)

    assert len(df) == 2
    assert df["verbruik"].tolist() == pytest.approx([0.15, 0.20])
    assert df["teruglevering"].tolist() == pytest.approx([0.20, 0.00])
    assert df["timestamp"].tolist() == [
        pd.Timestamp("2025-01-01 00:00:00"),
        pd.Timestamp("2025-01-01 00:15:00"),
    ]

def test_slimme_meter_portal_api_loader_single_tariff_fallback():
    # Single-tariff meters only populate the combined 'delivery' field.
    usages = [
        {"time": "01-01-2025 00:00:00 +0100", "delivery": "0,30", "returned_delivery_high": "0,10", "temperature": "5,0"},
        {"time": "01-01-2025 00:15:00 +0100", "delivery": "0,10", "returned_delivery_high": "0,00", "temperature": "5,0"},
    ]

    df = SlimmeMeterPortalAPILoader().load_usages(usages)

    assert df["verbruik"].tolist() == pytest.approx([0.30, 0.10])
    assert df["teruglevering"].tolist() == pytest.approx([0.10, 0.00])

def test_slimme_meter_portal_api_loader_dst_transition_uses_local_wall_time():
    # Europe/Amsterdam DST end 2025-10-26: offset shifts from +0200 to +0100.
    # Converting to local time and dropping tz must reflect that, not UTC.
    usages = [
        {"time": "26-10-2025 02:45:00 +0200", "delivery": "0,10", "returned_delivery_high": "0,00"},
        {"time": "26-10-2025 02:00:00 +0100", "delivery": "0,10", "returned_delivery_high": "0,00"},
    ]

    df = SlimmeMeterPortalAPILoader().load_usages(usages)

    assert df["timestamp"].tolist() == [
        pd.Timestamp("2025-10-26 02:00:00"),
        pd.Timestamp("2025-10-26 02:45:00"),
    ]

def test_slimme_meter_portal_api_loader_empty_raises():
    with pytest.raises(ValueError):
        SlimmeMeterPortalAPILoader().load_usages([])

def test_single_column_auto_detect():
    df = SmartLoader.load(Path(__file__).parent / "data" / "Kwartierdata_single_column.xlsx")

    assert len(df) == 3360
    assert "verbruik" in df.columns
    assert "teruglevering" in df.columns
    assert df["verbruik"].sum() == pytest.approx(255.846)
    assert df["teruglevering"].sum() == pytest.approx(0.0)
    assert (df["verbruik"] >= 0).all()
    assert (df["teruglevering"] >= 0).all()

def test_single_column_production_split(tmp_path):
    # Synthetic file with both positive (consumption) and negative (production) values
    xlsx_path = tmp_path / "single_column.xlsx"
    data = {
        "Datum Tijd": pd.date_range("2025-01-01", periods=4, freq="15min"),
        None: [0.5, -0.3, 0.0, -1.2],
    }
    pd.DataFrame(data).to_excel(xlsx_path, index=False)

    df = SmartLoader.load(xlsx_path)

    assert len(df) == 4
    assert df["verbruik"].tolist() == pytest.approx([0.5, 0.0, 0.0, 0.0])
    assert df["teruglevering"].tolist() == pytest.approx([0.0, 0.3, 0.0, 1.2])

def test_auto_detect_failure(tmp_path):
    csv_path = tmp_path / "unknown.csv"
    pd.DataFrame({"A": [1], "B": [2]}).to_csv(csv_path, index=False)
    
    with pytest.raises(ValueError, match="Could not automatically detect"):
        SmartLoader.load(csv_path)
