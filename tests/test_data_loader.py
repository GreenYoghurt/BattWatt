import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from data_loader import SmartLoader
import json
import io

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
