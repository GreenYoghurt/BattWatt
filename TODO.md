# Project TODOs

## Optimization & MPC (Completed)
- [x] **Add Battery Degradation Penalty to MPC**: Incorporate a cost factor for battery cycling (e.g., €/kWh throughput) into the MPC objective function to prevent unnecessary micro-cycling.
- [x] **Add VAT to MPC Objective**: Ensure the MPC controller accounts for VAT (21%) in its optimization logic to better align with the actual financial model.
- [x] **Add Selling Fee (Netbeheerders) to MPC**: Account for the per-kWh selling fees in the optimization.

## Features & Improvements
- [x] **Visual Progress Bar**: Added real-time simulation progress tracking in the Streamlit app.
- [x] **Custom Provider Configuration**: Enabled manual input for provider fees and settings in the UI.
- [ ] **Improve Load/PV Forecasting**: Move from "perfect forecast" to a simple persistence or rolling average model for more realistic MPC testing.
- [ ] **Automate Baseline Generation**: Add a flag to the E2E tests to automatically update the `simulation_baseline.csv` when intended.

## Architecture (Completed)
- [x] **Unified Simulation Engine**: Created `simulator.py` to centralize grid flow logic.
- [x] **Structured Results**: Introduced `models.py` for `SimulationResult`.
- [x] **Decoupled Billing**: Moved financial logic to `billing.py`.
- [x] **Net Metering Fix**: Corrected energy tax netting in `energy_providers.py`.
- [x] **Controller Standardization**: Established `BaseController` and moved logic to `controllers/` package.
