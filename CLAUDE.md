# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git Workflow

**Never commit or make changes directly on `main`.** Before starting any feature, fix, or other code change, create a dedicated branch first (e.g. `feature/<name>`, `fix/<name>`) and work there. If you notice mid-task that edits were made on `main` without a branch, stop and move the work to a new branch (`git checkout -b <name>`) before continuing or committing.

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
- `Controller_MPC`: Pyomo + HiGHS (`appsi_highs`, via the `highspy` package — no separate solver install needed) optimization over a configurable look-ahead horizon with plan caching

`app.py`'s strategy selector only exposes `Controller_PV` and `Controller_MPC`; `Controller_price` is currently only wired up in `example.py` and the test suite.

**`energy_providers.py` — Dutch market financials.** `Provider.calculate_flexible_costs()` handles two tax accounting regimes:
- `net_metering=True`: energy tax is applied to **net annual consumption** (import − export)
- `net_metering=False`: energy tax is applied to **gross import** per interval

This distinction is the most financially significant correctness constraint in the codebase.

**`billing.py` — cost accounting.** `BillingEngine` wraps a `Provider` and computes absolute bills and savings by comparing a baseline `SimulationResult` (no battery) against a simulated one.

**`data_loader.py` — ingestion.** `SmartLoader.load()` auto-detects file format among the registered `MeterDataLoader` subclasses (HomeWizard CSV, SlimmeMeterPortal Excel, single-column signed "Kwartierdata" Excel, standard DSO Excel); `SmartLoader.load(path, config=...)` uses `GenericMappedLoader` for a JSON column-mapping config instead. `SmartLoader.load_with_checks()` additionally runs `data_checks.py`'s data-quality checks (see below) and returns `(df, check_results)`. `merge_data()` joins meter data with ENTSO-E price data using nearest-timestamp merge. Raw ENTSO-E prices are in EUR/MWh and must be divided by 1000 before use. `SlimmeMeterPortalAPILoader` is a `MeterDataLoader` that is *not* part of SmartLoader's auto-detection (`can_handle()` always returns `False`) — it's driven explicitly via `load_usages()` on parsed API JSON from `slimmemeterportal_client.py`.

**`slimmemeterportal_client.py` — SlimmeMeterPortal UserAPI client.** `SlimmeMeterPortalClient` wraps the live UserAPI (connections + per-day usage) behind typed exceptions (`AuthenticationError`, `RateLimitError`, `BadRequestError`). `get_usage_range()` fetches a date range by issuing one request per day, sleeping and retrying on rate limits. Feeds `data_loader.SlimmeMeterPortalAPILoader.load_usages()`.

**`data_checks.py` — pre-simulation data quality checks.** `DataCheck` subclasses inspect the *raw* (pre-`validate()`) loader output and return an optional `CheckResult` (severity + message); `run_checks()` runs the registry. `app.py` surfaces failed checks as warnings the user must explicitly acknowledge before simulating. Only loaders that implement `get_raw_df()` (currently `HomeWizardLoader`) participate.

**`models.py`** — `SimulationResult` dataclass that carries both the timestep DataFrame (`df`) and aggregate totals.

**`app.py` — Streamlit UI.** Lets the user configure meter data source, one or more battery configs, provider fees, and strategy, then runs the baseline plus each battery simulation sequentially and renders a unified cost-breakdown comparison and per-battery charts. Not part of the core simulation pipeline — treat it as a consumer of `SmartLoader`/`Simulator`/`BillingEngine`, not a place to add simulation logic.

**`plotter.py` — Matplotlib plotting for the CLI examples** (`example.py`, `example_mpc.py`). Unrelated to `app.py`'s Plotly charts.

### DataFrame Conventions

The merged simulation DataFrame (passed to `Simulator.run()`) must have a `DatetimeIndex` and contain:
- `verbruik` — interval consumption (kWh)
- `teruglevering` — interval production/feed-in (kWh)
- `day_ahead_price` — price in EUR/kWh (after /1000 conversion)

After simulation, `Simulator` adds `adjusted_consumption`, `adjusted_production`, and `battery_soc` columns to this DataFrame.

### Test Baselines

`tests/test_e2e.py`, `tests/test_pv_controller.py`, `tests/test_mpc_e2e.py`, and `tests/test_conservation.py` contain **hardcoded financial expectations** (e.g., `expected_baseline_cost = 430.73`). Any logic change that shifts these values requires explicit justification before updating the constants. The optional `tests/simulation_baseline.csv` (and `tests/mpc_simulation_baseline.csv` for the MPC path) provides a per-timestep regression reference.

### Controllers Package vs. Top-Level Files

The active controllers live in `controllers/` (the package). Top-level `controller_PV.py` and `controller_price.py` are legacy files. `example.py`/`example_mpc.py` import directly from the specific submodule (`from controllers.controller_MPC import Controller_MPC`); `app.py` imports from the package root (`from controllers import Controller_PV, Controller_MPC`), which re-exports the same classes via `controllers/__init__.py`.

### User-Facing Docs

`docs/cost-calculation.md`, `docs/optimization-and-control.md`, and `docs/assumptions-and-limitations.md` explain the billing formulas, controller behavior, and known modeling gaps (e.g. PV curtailment isn't representable at all — see that doc) in human-readable form. When a change here alters a formula, a controller's decision logic, or a modeling assumption, update the matching doc page too.
