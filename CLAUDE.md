# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```powershell
# Run the Streamlit web app
streamlit run app.py

# Run example simulations (CLI)
python example.py        # Rule-based (PV + Price controller)
python example_mpc.py    # MPC optimization-based

# Run all tests (must use -m to resolve module imports correctly)
python -m pytest -s tests/

# Run a single test
python -m pytest -s tests/test_e2e.py::test_battwatt_e2e_simulation
```

Run commands individually — do not chain with `&&` or `;` in PowerShell.

## Architecture

The simulation pipeline follows a fixed flow:

```
DataLoader → merge_data() → Simulator(Battery, Controller) → SimulationResult → BillingEngine
```

### Core Components

**`simulator.py` — the main loop.** `Simulator.run()` is the only place where grid flow is computed. The formula `net_grid_energy = (production - consumption) - (to_battery - from_battery) + (to_grid - from_grid)` must not be reimplemented elsewhere.

**`battery.py` — physics only.** `Battery` tracks SoC and enforces physical limits (capacity, power, efficiency). `battery.step()` is called by `Simulator`, not by controllers. Use `get_battery(name)` for predefined Bliq configurations.

**`controllers/` — strategy layer.** All controllers inherit from `BaseController` (defined in `controllers/controller_PV.py`) and implement a single `step(production, consumption, datetime_index, duration_hours)` returning `(to_battery, from_battery)` as intention signals — the `Simulator` applies the battery physics afterwards.
- `Controller_PV`: maximizes PV self-consumption
- `Controller_price`: rule-based using daily 20th/80th price quantiles
- `Controller_MPC`: Pyomo + HiGHS optimization over a configurable look-ahead horizon with plan caching

**`energy_providers.py` — Dutch market financials.** `Provider.calculate_flexible_costs()` handles two tax accounting regimes:
- `net_metering=True`: energy tax is applied to **net annual consumption** (import − export)
- `net_metering=False`: energy tax is applied to **gross import** per interval

This distinction is the most financially significant correctness constraint in the codebase.

**`billing.py` — cost accounting.** `BillingEngine` wraps a `Provider` and computes absolute bills and savings by comparing a baseline `SimulationResult` (no battery) against a simulated one.

**`data_loader.py` — ingestion.** `SmartLoader.load()` auto-detects file format (HomeWizard CSV or DSO Excel). `merge_data()` joins meter data with ENTSO-E price data using nearest-timestamp merge. Raw ENTSO-E prices are in EUR/MWh and must be divided by 1000 before use.

**`models.py`** — `SimulationResult` dataclass that carries both the timestep DataFrame (`df`) and aggregate totals.

### DataFrame Conventions

The merged simulation DataFrame (passed to `Simulator.run()`) must have a `DatetimeIndex` and contain:
- `verbruik` — interval consumption (kWh)
- `teruglevering` — interval production/feed-in (kWh)
- `day_ahead_price` — price in EUR/kWh (after /1000 conversion)

After simulation, `Simulator` adds `adjusted_consumption`, `adjusted_production`, and `battery_soc` columns to this DataFrame.

### Test Baselines

`tests/test_e2e.py` and `tests/test_pv_controller.py` contain **hardcoded financial expectations** (e.g., `expected_baseline_cost = 443.30`). Any logic change that shifts these values requires explicit justification before updating the constants. The optional `tests/simulation_baseline.csv` provides a per-timestep regression reference.

### Controllers Package vs. Top-Level Files

The active controllers live in `controllers/` (the package). Top-level `controller_PV.py` and `controller_price.py` are legacy files — imports in `example.py` and `app.py` use `from controllers.controller_* import ...`.
