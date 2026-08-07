# Project TODOs

## Optimization & MPC (Completed)
- [x] **Add Battery Degradation Penalty to MPC**: Incorporate a cost factor for battery cycling (e.g., €/kWh throughput) into the MPC objective function to prevent unnecessary micro-cycling.
- [x] **Add VAT to MPC Objective**: Ensure the MPC controller accounts for VAT (21%) in its optimization logic to better align with the actual financial model.
- [x] **Add Selling Fee (Netbeheerders) to MPC**: Account for the per-kWh selling fees in the optimization.

## Features & Improvements
- [x] **Visual Progress Bar**: Added real-time simulation progress tracking in the Streamlit app.
- [x] **Custom Provider Configuration**: Enabled manual input for provider fees and settings in the UI.
- [x] **Multi-Format Data Import**: Added auto-detected loaders for SlimmeMeterPortal.nl Excel and single-column signed "Kwartierdata" Excel, plus a generic JSON column-mapping loader for unsupported formats (`data_loader.py`).
- [x] **Data Quality Checks**: Added an extensible `DataCheck` framework (`data_checks.py`) that warns on likely file corruption (e.g. Excel-truncated HomeWizard CSVs) before simulating.
- [x] **SlimmeMeterPortal UserAPI Integration**: Added a typed API client (`slimmemeterportal_client.py`) so users can fetch meter data live instead of uploading a file.
- [x] **Multi-Battery Comparison**: The web app can run several battery configurations sequentially in one simulation and compare them in a unified breakdown table and toggleable charts.
- [x] **Itemised Cost Breakdown**: Added a per-component cost breakdown (market price, energy tax, supplier mark-ups, feed-in revenue, fixed costs) comparing baseline vs. simulated results.
- [x] **Privacy Notice**: Added a no-data-retention statement to the app header.
- [ ] **Improve Load/PV Forecasting**: Move from "perfect forecast" to a simple persistence or rolling average model for more realistic MPC testing.
- [ ] **Automate Baseline Generation**: Add a flag to the E2E tests to automatically update the `simulation_baseline.csv` when intended.
- [ ] **Expose Price Controller in the Web App**: `Controller_price` (rule-based price-quantile strategy) is only wired up in `example.py`/tests; the `app.py` strategy selector currently only offers PV and MPC.
- [ ] **Provider Presets in the Web App**: `streamlit_strategy.md` proposed a dropdown of common Dutch providers (Zonneplan, Tibber, Frank Energie); the sidebar currently only supports manual fee entry (`energy_providers.get_providers()` still only defines a single "Zonneplan" preset, used by the CLI examples and tests).
- [ ] **Net Metering Toggle in the Web App**: `app.py` hardcodes `net_metering=False` on the `Provider` it builds; there's no UI control to evaluate the saldering-enabled scenario.

## Architecture (Completed)
- [x] **Unified Simulation Engine**: Created `simulator.py` to centralize grid flow logic.
- [x] **Structured Results**: Introduced `models.py` for `SimulationResult`.
- [x] **Decoupled Billing**: Moved financial logic to `billing.py`.
- [x] **Net Metering Fix**: Corrected energy tax netting in `energy_providers.py`.
- [x] **Controller Standardization**: Established `BaseController` and moved logic to `controllers/` package.
