import warnings

import numpy as np
import pandas as pd

from battery import Battery
from controllers.controller_PV import Controller_PV
from simulator import Simulator


def _run(df):
    battery = Battery(capacity_kwh=0.0, max_charge_kw=0.0, max_discharge_kw=0.0, standby_power_w=0.0)
    return Simulator(battery, Controller_PV(battery)).run(df, progress_callback=lambda *_: None)


def test_duplicate_timestamps_keep_their_own_values():
    # DST fall-back exports repeat a quarter-hour. Each row must keep its own
    # result; label-based `.at[index, col]` used to overwrite both rows.
    idx = pd.DatetimeIndex(["2025-10-26 02:00", "2025-10-26 02:00", "2025-10-26 02:15"])
    df = pd.DataFrame({"verbruik": [0.5, 0.2, 0.1], "teruglevering": [0.0, 0.0, 0.0]}, index=idx)

    result = _run(df)

    np.testing.assert_allclose(result.df["adjusted_consumption"].to_numpy(), [0.5, 0.2, 0.1])
    assert result.total_adjusted_consumption_kwh == pytest_approx(0.8)


def test_result_columns_are_float_and_no_pandas_deprecation_warnings():
    idx = pd.date_range("2025-01-01", periods=4, freq="15min")
    df = pd.DataFrame({"verbruik": [0.1, 0.0, 0.3, 0.0], "teruglevering": [0.0, 0.2, 0.0, 0.4]}, index=idx)

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any FutureWarning/Pandas4Warning fails the test
        result = _run(df)

    for col in ("battery_soc", "adjusted_production", "adjusted_consumption"):
        assert result.df[col].dtype == float
    np.testing.assert_allclose(result.df["adjusted_production"].to_numpy(), [0.0, 0.2, 0.0, 0.4])


def pytest_approx(value):
    import pytest

    return pytest.approx(value)
