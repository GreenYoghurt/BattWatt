import numpy as np
import pandas as pd
from pathlib import Path
from entsoe import EntsoePandasClient
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List, Type, Union
import json
import io

def fetch_entsoe_prices(api_key: str, start_date: pd.Timestamp, end_date: pd.Timestamp, country_code: str = 'NL') -> pd.DataFrame:
    """
    Fetch day-ahead electricity prices from ENTSO-E API.
    """
    client = EntsoePandasClient(api_key=api_key)
    
    if start_date.tz is None:
        start_date = start_date.tz_localize('Europe/Amsterdam')
    if end_date.tz is None:
        end_date = end_date.tz_localize('Europe/Amsterdam')

    try:
        prices_series = client.query_day_ahead_prices(country_code, start=start_date, end=end_date)
    except Exception as e:
        raise RuntimeError(f"Error fetching data from ENTSO-E: {e}")

    df = prices_series.reset_index()
    df.columns = ['timestamp', 'day_ahead_price']
    df['timestamp'] = df['timestamp'].dt.tz_convert(None)
    
    return df

def load_price_data(path: Union[str, Path, Any]) -> pd.DataFrame:
    """
    Laad en verwerk de day-ahead prijsdata (ENTSO-E Excel export format).
    """
    df = pd.read_excel(path)

    time_col = "MTU (CET/CEST)"
    price_col = "Day-ahead Price (EUR/MWh)"
    if time_col not in df.columns or price_col not in df.columns:
        raise ValueError(
            f"Verwachte kolommen '{time_col}' en '{price_col}' niet gevonden in prijsbestand."
        )

    time_str = df[time_col].astype(str)
    start_str = time_str.str.split(" - ").str[0]
    start_str = (
        start_str.str.replace(" (CET)", "", regex=False)
        .str.replace(" (CEST)", "", regex=False)
    )

    df["timestamp"] = pd.to_datetime(start_str, errors="coerce", dayfirst=True)
    df = df.dropna(subset=["timestamp"]).copy()
    df["day_ahead_price"] = df[price_col].astype(float)

    return df[["timestamp", "day_ahead_price"]].sort_values("timestamp")

# --- New Modular Data Loading System ---

class MeterDataLoader(ABC):
    """Base class for meter data loaders."""
    
    @abstractmethod
    def can_handle(self, path: Any) -> bool:
        """Check if this loader can handle the given file."""
        pass

    @abstractmethod
    def load(self, path: Any) -> pd.DataFrame:
        """Load and process the data."""
        pass

    def _get_name(self, path: Any) -> str:
        if isinstance(path, (str, Path)):
            return str(path)
        return getattr(path, 'name', '')

    def validate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Standardize and validate the output DataFrame."""
        required = ["timestamp", "verbruik", "teruglevering"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Loader failed to produce required columns: {missing}")
        
        df = df.dropna(subset=["timestamp"]).copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp")

        # Check for duplicates/overlaps
        if df["timestamp"].duplicated().any():
            # Sum duplicates at the same timestamp
            df = df.groupby("timestamp")[["verbruik", "teruglevering"]].sum().reset_index()
        
        # Check for gaps
        if len(df) > 1:
            diffs = df["timestamp"].diff().dropna()
            mode_diff = diffs.mode()[0]
            gaps = diffs > mode_diff * 1.5
            if gaps.any():
                num_gaps = gaps.sum()
                max_gap = diffs.max()
                # We log/print a warning for now, could be a logger in the future
                print(f"Warning: Detected {num_gaps} gaps in data. Largest gap: {max_gap}")
        
        # Ensure numeric
        df["verbruik"] = pd.to_numeric(df["verbruik"], errors="coerce").fillna(0)
        df["teruglevering"] = pd.to_numeric(df["teruglevering"], errors="coerce").fillna(0)
        
        # Physical check
        if (df["verbruik"] < 0).any() or (df["teruglevering"] < 0).any():
            df.loc[df["verbruik"] < 0, "verbruik"] = 0
            df.loc[df["teruglevering"] < 0, "teruglevering"] = 0
            
        return df[["timestamp", "verbruik", "teruglevering"]].sort_values("timestamp")

class HomeWizardLoader(MeterDataLoader):
    """Loader for HomeWizard CSV exports."""
    
    def can_handle(self, path: Any) -> bool:
        name = self._get_name(path)
        if not name.lower().endswith(".csv"):
            return False
        try:
            # Reset buffer if it's a file-like object
            if hasattr(path, 'seek'): path.seek(0)
            df_head = pd.read_csv(path, nrows=1)
            # Standard HomeWizard headers
            return "Import T1 kWh" in df_head.columns or "time" in df_head.columns
        except:
            return False

    def load(self, path: Any) -> pd.DataFrame:
        if hasattr(path, 'seek'): path.seek(0)
        df = pd.read_csv(path)
        df["timestamp"] = pd.to_datetime(df["time"], errors="coerce")
        
        # Cumulative to interval
        t1_imp = df.get("Import T1 kWh", 0)
        t2_imp = df.get("Import T2 kWh", 0)
        t1_exp = df.get("Export T1 kWh", 0)
        t2_exp = df.get("Export T2 kWh", 0)

        total_import = t1_imp + t2_imp
        total_export = t1_exp + t2_exp
        
        df["verbruik"] = total_import.diff().fillna(0)
        df["teruglevering"] = total_export.diff().fillna(0)
        
        return self.validate(df)

class StandardExcelLoader(MeterDataLoader):
    """Loader for the 'standard' Excel format (e.g. from some DSOs)."""
    
    def can_handle(self, path: Any) -> bool:
        name = self._get_name(path)
        if not (name.lower().endswith(".xlsx") or name.lower().endswith(".xls")):
            return False
        try:
            if hasattr(path, 'seek'): path.seek(0)
            df_head = pd.read_excel(path, nrows=5)
            return "levering_normaal" in df_head.columns or "Van" in df_head.columns
        except:
            return False

    def load(self, path: Any) -> pd.DataFrame:
        if hasattr(path, 'seek'): path.seek(0)
        df = pd.read_excel(path)
        
        # Try to find timestamp column
        if "datum_tijd" in df.columns:
            dt_str = df["datum_tijd"].astype(str)
            dt_no_tz = dt_str.str.extract(r"(^\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2})")[0]
            df["timestamp"] = pd.to_datetime(dt_no_tz, format="%d-%m-%Y %H:%M:%S", errors="coerce")
        elif "Van" in df.columns:
            df["timestamp"] = pd.to_datetime(df["Van"], errors="coerce")

        num_cols = ["levering_normaal", "levering_laag", "teruglevering_normaal", "teruglevering_laag", "Verbruik (kWh)", "Teruglevering (kWh)"]
        for col in num_cols:
            if col in df.columns:
                if not pd.api.types.is_numeric_dtype(df[col]):
                    df[col] = df[col].astype(str).str.replace(",", ".", regex=False)
                    df[col] = pd.to_numeric(df[col], errors="coerce")

        if "levering_normaal" in df.columns:
            df["verbruik"] = df["levering_normaal"].fillna(0) + df["levering_laag"].fillna(0)
            df["teruglevering"] = df["teruglevering_normaal"].fillna(0) + df["teruglevering_laag"].fillna(0)
        elif "Verbruik (kWh)" in df.columns:
            df["verbruik"] = df["Verbruik (kWh)"].fillna(0)
            df["teruglevering"] = df["Teruglevering (kWh)"].fillna(0)
        
        return self.validate(df)

class GenericMappedLoader(MeterDataLoader):
    """Loader that uses a mapping dictionary for arbitrary formats."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def can_handle(self, path: Any) -> bool:
        return True

    def load(self, path: Any) -> pd.DataFrame:
        if hasattr(path, 'seek'): path.seek(0)
        fmt = self.config.get("format", "csv")
        sep = self.config.get("delimiter", ",")
        dec = self.config.get("decimal", ".")
        cols = self.config.get("columns", {})
        is_cumulative = self.config.get("is_cumulative", False)

        if fmt == "csv":
            df = pd.read_csv(path, sep=sep, decimal=dec)
        else:
            df = pd.read_excel(path)

        name = self._get_name(path)
        # Better error handling for missing columns
        for col_key, col_name in cols.items():
            if isinstance(col_name, list):
                missing = [c for c in col_name if c not in df.columns]
                if missing:
                    raise ValueError(f"Column(s) {missing} (from mapping '{col_key}') not found in {name}. Available columns: {df.columns.tolist()}")
            elif col_name not in df.columns:
                raise ValueError(f"Column '{col_name}' (from mapping '{col_key}') not found in {name}. Available columns: {df.columns.tolist()}")

        df["timestamp"] = pd.to_datetime(df[cols["timestamp"]], errors="coerce")
        imp_col = cols.get("import")
        exp_col = cols.get("export")

        # Support list of columns to sum
        if isinstance(imp_col, list):
            df["verbruik"] = df[imp_col].sum(axis=1)
        else:
            df["verbruik"] = df[imp_col]

        if isinstance(exp_col, list):
            df["teruglevering"] = df[exp_col].sum(axis=1)
        else:
            df["teruglevering"] = df[exp_col]

        if is_cumulative:
            df["verbruik"] = df["verbruik"].diff().fillna(0)
            df["teruglevering"] = df["teruglevering"].diff().fillna(0)
            return self.validate(df.iloc[1:])

        return self.validate(df)

class SmartLoader:
    """Main entry point for loading meter data with auto-detection."""
    
    _loaders: List[Type[MeterDataLoader]] = [HomeWizardLoader, StandardExcelLoader]

    @classmethod
    def load(cls, path: Any, config: Optional[Union[Dict[str, Any], str, Path]] = None) -> pd.DataFrame:
        # 1. Handle config (dict or file path)
        if config:
            if isinstance(config, (str, Path)):
                config_path = Path(config)
                with open(config_path, 'r') as f:
                    config = json.load(f)
            return GenericMappedLoader(config).load(path)
        
        # 2. Try predefined loaders
        for loader_cls in cls._loaders:
            loader = loader_cls()
            if loader.can_handle(path):
                return loader.load(path)
        
        # 3. Last resort: Try to read headers and provide a helpful error
        name = getattr(path, 'name', str(path))
        try:
            if hasattr(path, 'seek'): path.seek(0)
            if name.lower().endswith(".csv"):
                headers = pd.read_csv(path, nrows=0).columns.tolist()
            else:
                headers = pd.read_excel(path, nrows=0).columns.tolist()
            
            raise ValueError(
                f"Could not automatically detect the format of '{name}'.\n"
                f"Available headers: {headers}\n"
                "Please provide a mapping configuration or use a supported format."
            )
        except Exception as e:
            if isinstance(e, ValueError): raise e
            raise ValueError(f"Could not read or detect format for file: {name}. Error: {e}")

# --- Backward Compatibility Wrappers ---

def load_meter_data_HomeWizzard(path: Any) -> pd.DataFrame:
    return HomeWizardLoader().load(path)

def load_meter_data2(path: Any) -> pd.DataFrame:
    return StandardExcelLoader().load(path)

def merge_data(
    meter_df: pd.DataFrame,
    price_df: pd.DataFrame,
    tolerance: str = "15min",
) -> pd.DataFrame:
    """
    Merge meter-data met prijsdata op dichtstbijzijnde timestamp.
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
