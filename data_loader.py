import numpy as np
import pandas as pd
from pathlib import Path
from entsoe import EntsoePandasClient

def fetch_entsoe_prices(api_key: str, start_date: pd.Timestamp, end_date: pd.Timestamp, country_code: str = 'NL') -> pd.DataFrame:
    """
    Fetch day-ahead electricity prices from ENTSO-E API.
    
    Parameters:
    - api_key: Your ENTSO-E API key.
    - start_date: Start of the period (timezone-aware recommended).
    - end_date: End of the period (timezone-aware recommended).
    - country_code: Bidding zone code (default 'NL').
    
    Returns:
    - DataFrame with 'timestamp' and 'day_ahead_price' (EUR/MWh).
    """
    client = EntsoePandasClient(api_key=api_key)
    
    # Ensure timestamps are timezone-aware (ENTSO-E requires this)
    if start_date.tz is None:
        start_date = start_date.tz_localize('Europe/Amsterdam')
    if end_date.tz is None:
        end_date = end_date.tz_localize('Europe/Amsterdam')

    # Query prices
    try:
        prices_series = client.query_day_ahead_prices(country_code, start=start_date, end=end_date)
    except Exception as e:
        raise RuntimeError(f"Error fetching data from ENTSO-E: {e}")

    # Convert Series to DataFrame
    df = prices_series.reset_index()
    df.columns = ['timestamp', 'day_ahead_price']
    
    # Standardize to local time (naive) for internal processing if needed, 
    # but keeping it aware is safer for merging.
    df['timestamp'] = df['timestamp'].dt.tz_convert(None)
    
    return df

def load_price_data(path: Path) -> pd.DataFrame:
    """
    Laad en verwerk de day-ahead prijsdata.

    Verwacht een kolom 'MTU (CET/CEST)' met een tijdsrange:
        '01/01/2025 00:00:00 - 01/01/2025 00:15:00 (CET)'
    Neemt de starttijd als timestamp.
    """
    df = pd.read_excel(path)

    # Kolomnaam check (optioneel robuustheid)
    time_col = "MTU (CET/CEST)"
    price_col = "Day-ahead Price (EUR/MWh)"
    if time_col not in df.columns or price_col not in df.columns:
        raise ValueError(
            f"Verwachte kolommen '{time_col}' en '{price_col}' niet gevonden in prijsbestand."
        )

    # Starttijd uit de MTU-string halen
    time_str = df[time_col].astype(str)
    start_str = time_str.str.split(" - ").str[0]

    # Eventuele ' (CET)' / ' (CEST)' suffixen verwijderen
    start_str = (
        start_str.str.replace(" (CET)", "", regex=False)
        .str.replace(" (CEST)", "", regex=False)
    )

    df["timestamp"] = pd.to_datetime(start_str, errors="coerce", dayfirst=True)
    df = df.dropna(subset=["timestamp"]).copy()

    df["day_ahead_price"] = df[price_col].astype(float)

    return df[["timestamp", "day_ahead_price"]].sort_values("timestamp")


def load_meter_data2(path: Path) -> pd.DataFrame:
    """
    Laad en verwerk de slimme-meter data.

    Verwacht kolommen:
        - 'datum_tijd' (bijv. '01-10-2025 00:00:00 +0200')
        - 'levering_normaal', 'levering_laag'
        - 'teruglevering_normaal', 'teruglevering_laag'
    waarin decimale komma's worden gebruikt.
    """
    df = pd.read_excel(path)

    required_cols = [
        "datum_tijd",
        "levering_normaal",
        "levering_laag",
        "teruglevering_normaal",
        "teruglevering_laag",
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Ontbrekende kolommen in data-bestand: {missing}")

    # Datum/tijd zonder timezone-parsing (offset verwijderen)
    dt_str = df["datum_tijd"].astype(str)
    # Pak alleen "dd-mm-YYYY HH:MM:SS"
    dt_no_tz = dt_str.str.extract(r"(^\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2})")[0]
    df["timestamp"] = pd.to_datetime(dt_no_tz, format="%d-%m-%Y %H:%M:%S", errors="coerce")
    df = df.dropna(subset=["timestamp"]).copy()

    # Numerieke kolommen met komma als decimaal
    num_cols = [
        "levering_normaal",
        "levering_laag",
        "teruglevering_normaal",
        "teruglevering_laag",
    ]
    for col in num_cols:
        df[col] = (
            df[col]
            .astype(str)
            .str.replace(",", ".", regex=False)
            .replace("nan", np.nan)
            .astype(float)
        )

    # Totale verbruik en teruglevering per kwartier
    df["verbruik"] = df["levering_normaal"].fillna(0) + df["levering_laag"].fillna(0)
    df["teruglevering"] = (
        df["teruglevering_normaal"].fillna(0) + df["teruglevering_laag"].fillna(0)
    )

    return df.sort_values("timestamp")


def load_meter_data_HomeWizzard(path: Path) -> pd.DataFrame:
    """
    Laad en verwerk de slimme-meter data van HomeWizzard (CSV).

    Verwacht kolommen:
        - 'time' (bijv. '2025-01-01 00:00')
        - 'Import T1 kWh', 'Import T2 kWh'
        - 'Export T1 kWh', 'Export T2 kWh'
    De meterstanden zijn cumulatief, dus we berekenen het verschil tussen de rijen.
    """
    df = pd.read_csv(path)

    required_cols = [
        "time",
        "Import T1 kWh",
        "Import T2 kWh",
        "Export T1 kWh",
        "Export T2 kWh",
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Ontbrekende kolommen in HomeWizzard data-bestand: {missing}")

    # Converteren naar datetime
    df["timestamp"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).copy()
    df = df.sort_values("timestamp")

    # Numerieke kolommen (meterstanden)
    num_cols = [
        "Import T1 kWh",
        "Import T2 kWh",
        "Export T1 kWh",
        "Export T2 kWh",
    ]
    for col in num_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Bereken cumulatieve totalen
    df["total_import"] = df["Import T1 kWh"] + df["Import T2 kWh"]
    df["total_export"] = df["Export T1 kWh"] + df["Export T2 kWh"]

    # Verschil berekenen voor intervalverbruik (kWh per kwartier)
    df["verbruik"] = df["total_import"].diff().fillna(0)
    df["teruglevering"] = df["total_export"].diff().fillna(0)

    # Voorkom negatieve waarden bijv. door meter-resets
    df.loc[df["verbruik"] < 0, "verbruik"] = 0
    df.loc[df["teruglevering"] < 0, "teruglevering"] = 0

    return df


def merge_data(
    meter_df: pd.DataFrame,
    price_df: pd.DataFrame,
    tolerance: str = "15min",
) -> pd.DataFrame:
    """
    Merge meter-data met prijsdata op dichtstbijzijnde timestamp.

    Parameters
    ----------
    meter_df : DataFrame met kolom 'timestamp'.
    price_df : DataFrame met kolommen 'timestamp' en 'day_ahead_price'.
    tolerance : maximale tijdsafstand voor match (bijv. '15min').

    Returns
    -------
    DataFrame met gecombineerde data.
    """
    merged = pd.merge_asof(
        meter_df.sort_values("timestamp"),
        price_df.sort_values("timestamp"),
        on="timestamp",
        direction="nearest",
        tolerance=pd.Timedelta(tolerance),
    )
    merged = merged.dropna(subset=["day_ahead_price"]).copy()
    return merged
