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

def test_auto_detect_failure(tmp_path):
    csv_path = tmp_path / "unknown.csv"
    pd.DataFrame({"A": [1], "B": [2]}).to_csv(csv_path, index=False)
    
    with pytest.raises(ValueError, match="Could not automatically detect"):
        SmartLoader.load(csv_path)
