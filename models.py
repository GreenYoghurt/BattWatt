from dataclasses import dataclass
import pandas as pd

@dataclass
class SimulationResult:
    """
    Holds the output of a battery simulation.
    """
    df: pd.DataFrame
    total_production_kwh: float
    total_consumption_kwh: float
    total_adjusted_production_kwh: float
    total_adjusted_consumption_kwh: float
    final_soc_pct: float
    final_soc_kwh: float
    delta_soc_kwh: float
