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

    def get_raw_df(self, path: Any) -> Optional[pd.DataFrame]:
        """Return the intermediate DataFrame before column stripping, for data quality checks.
        Override in loaders that carry extra columns (e.g. L1/L2/L3 power)."""
        return None

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
            if hasattr(path, 'seek'): path.seek(0)
            df_head = pd.read_csv(path, nrows=1)
            return "Import T1 kWh" in df_head.columns or "time" in df_head.columns
        except:
            return False

    def _read_raw(self, path: Any) -> pd.DataFrame:
        """Read the CSV and compute interval columns, keeping all original columns intact."""
        if hasattr(path, 'seek'): path.seek(0)
        df = pd.read_csv(path)
        df["timestamp"] = pd.to_datetime(df["time"], errors="coerce")

        t1_imp = df.get("Import T1 kWh", 0)
        t2_imp = df.get("Import T2 kWh", 0)
        t1_exp = df.get("Export T1 kWh", 0)
        t2_exp = df.get("Export T2 kWh", 0)

        total_import = t1_imp + t2_imp
        total_export = t1_exp + t2_exp

        df["verbruik"] = total_import.diff().fillna(0)
        df["teruglevering"] = total_export.diff().fillna(0)

        return df

    def load(self, path: Any) -> pd.DataFrame:
        return self.validate(self._read_raw(path))

    def get_raw_df(self, path: Any) -> pd.DataFrame:
        return self._read_raw(path)

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

class SlimmeMeterPortalLoader(MeterDataLoader):
    """Loader for 'Slimme Meter Portal' Excel exports."""

    TIME_COL = "Tijdstip"
    NUM_COLS = [
        "levering normaaltarief [kWh]",
        "levering laagtarief [kWh]",
        "teruglevering normaaltarief [kWh]",
        "teruglevering laagtarief [kWh]",
    ]

    def can_handle(self, path: Any) -> bool:
        name = self._get_name(path)
        if not (name.lower().endswith(".xlsx") or name.lower().endswith(".xls")):
            return False
        try:
            if hasattr(path, 'seek'): path.seek(0)
            df_head = pd.read_excel(path, nrows=5)
            return self.TIME_COL in df_head.columns and "levering normaaltarief [kWh]" in df_head.columns
        except:
            return False

    def load(self, path: Any) -> pd.DataFrame:
        if hasattr(path, 'seek'): path.seek(0)
        df = pd.read_excel(path)

        df["timestamp"] = pd.to_datetime(df[self.TIME_COL], errors="coerce")

        for col in self.NUM_COLS:
            if col in df.columns and not pd.api.types.is_numeric_dtype(df[col]):
                df[col] = df[col].astype(str).str.replace(",", ".", regex=False)
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df["verbruik"] = df["levering normaaltarief [kWh]"].fillna(0) + df["levering laagtarief [kWh]"].fillna(0)
        df["teruglevering"] = df["teruglevering normaaltarief [kWh]"].fillna(0) + df["teruglevering laagtarief [kWh]"].fillna(0)

        return self.validate(df)

class SingleColumnLoader(MeterDataLoader):
    """Loader for Kwartierdata exports with a single signed net-power column
    (positive = consumption, negative = production)."""

    TIME_COL = "Datum Tijd"

    def can_handle(self, path: Any) -> bool:
        name = self._get_name(path)
        if not (name.lower().endswith(".xlsx") or name.lower().endswith(".xls")):
            return False
        try:
            if hasattr(path, 'seek'): path.seek(0)
            df_head = pd.read_excel(path, nrows=5)
            if len(df_head.columns) != 2:
                return False
            first_col = str(df_head.columns[0]).strip().lower()
            second_col = df_head.columns[1]
            return first_col == self.TIME_COL.lower() and (pd.isna(second_col) or str(second_col).startswith("Unnamed"))
        except:
            return False

    def load(self, path: Any) -> pd.DataFrame:
        if hasattr(path, 'seek'): path.seek(0)
        df = pd.read_excel(path)

        time_col, value_col = df.columns[0], df.columns[1]
        df["timestamp"] = pd.to_datetime(df[time_col], errors="coerce")

        value = pd.to_numeric(df[value_col], errors="coerce").fillna(0)
        df["verbruik"] = value.clip(lower=0)
        df["teruglevering"] = (-value).clip(lower=0)

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

        value_col = cols.get("value")
        if value_col is not None:
            # Single signed column: positive = consumption, negative = production
            value = pd.to_numeric(df[value_col], errors="coerce")
            if is_cumulative:
                value = value.diff()
            df["verbruik"] = value.clip(lower=0)
            df["teruglevering"] = (-value).clip(lower=0)
        else:
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

        if is_cumulative:
            return self.validate(df.iloc[1:])

        return self.validate(df)

class SmartLoader:
    """Main entry point for loading meter data with auto-detection."""

    _loaders: List[Type[MeterDataLoader]] = [HomeWizardLoader, SlimmeMeterPortalLoader, SingleColumnLoader, StandardExcelLoader]

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

    @classmethod
    def load_with_checks(
        cls,
        path: Any,
        config: Optional[Union[Dict[str, Any], str, Path]] = None,
    ) -> "tuple[pd.DataFrame, list]":
        """Load meter data and run data quality checks.

        Returns (validated_df, check_results). check_results is empty when no
        checks are applicable (e.g. custom-mapped formats without phase columns).
        """
        from data_checks import run_checks

        if config:
            if isinstance(config, (str, Path)):
                with open(config, 'r') as f:
                    config = json.load(f)
            return GenericMappedLoader(config).load(path), []

        for loader_cls in cls._loaders:
            loader = loader_cls()
            if loader.can_handle(path):
                raw_df = loader.get_raw_df(path)
                if raw_df is not None:
                    validated_df = loader.validate(raw_df)
                    checks = run_checks(raw_df)
                else:
                    validated_df = loader.load(path)
                    checks = []
                return validated_df, checks

        # Fall through to the standard error path
        return cls.load(path), []

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
