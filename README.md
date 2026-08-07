# BattWatt

BattWatt is a Python-based tool designed to evaluate and simulate household energy consumption, solar PV production, and battery storage performance using Dutch day-ahead energy prices. It allows users to estimate potential savings by optimizing battery usage based on price fluctuations and solar availability.

## Features

- **Interactive Web App**: User-friendly Streamlit interface for easy data upload, multi-battery comparison, and simulation. Runs data in-memory only — nothing is stored (see the privacy notice in the app header).
- **Data Loading**:
  - Automatic format detection for smart meter (P1) exports: HomeWizard CSV, SlimmeMeterPortal.nl Excel, single-column "Kwartierdata" Excel, and standard DSO Excel exports.
  - Live import via the **SlimmeMeterPortal UserAPI** (no file upload needed).
  - A generic column-mapping loader for unsupported/custom CSV or Excel formats.
  - Built-in data quality checks (e.g. detecting HomeWizard CSVs corrupted by Excel round-tripping) surfaced as warnings before simulating.
  - Automated Day-Ahead price fetching via the **ENTSO-E API**, or manual import from ENTSO-E Excel exports.
- **Battery Simulation**: Realistic battery modeling including capacity, charge/discharge limits, and efficiency losses. Compare multiple battery configurations side by side in a single run.
- **Advanced Controllers**:
  - **PV Controller**: Maximizes self-consumption of solar energy.
  - **Price Controller**: Rule-based optimization using daily price quantiles (available at the simulation-engine/CLI level; not currently exposed in the web app strategy selector).
  - **MPC Controller**: Optimization-based (Model Predictive Control) using `Pyomo` and the `HiGHS` solver (via `highspy`) for cost-optimal behavior, with plan caching for performance.
- **Financial Analysis**: Comprehensive billing engine accounting for:
  - Dutch energy taxes and VAT (21%).
  - Net Metering (Salderingsregeling).
  - Custom provider fees (Subscription, Buying/Selling mark-ups).
  - Itemised cost-breakdown table comparing baseline vs. each simulated battery.
- **Visualization**: Interactive Plotly charts (energy flow, SoC, market price) and a visual progress bar for long-running simulations.

## Installation

### Prerequisites
- Python 3.12 or higher
- No separate HiGHS install is required — the solver ships as the `highspy` Python package in `requirements.txt`.

### Setup
1. Clone the repository.
2. Create and activate a virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1  # Windows
   ```
3. Install the required dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

## Usage

### Web Application (Recommended)
Launch the interactive dashboard:
```powershell
streamlit run app.py
```
1. In the sidebar, choose a meter data source: upload a file (auto-detected format, or a custom column mapping) or fetch data live via the SlimmeMeterPortal API.
2. Add one or more battery configurations to compare (presets or custom capacity/power/efficiency).
3. Configure your energy provider's fees and choose a control strategy (PV self-consumption or MPC cost-optimal).
4. An ENTSO-E API key must be set in `.streamlit/secrets.toml` (as `ENTSOE_API_KEY`) for automated price fetching — the **🚀 Start Simulatie** button is disabled without it. A `SLIMMEMETERPORTAL_API_KEY` secret can also be set to pre-fill the SlimmeMeterPortal API key field.
5. Click **🚀 Start Simulatie** and review the results overview, cost breakdown, and interactive charts.

### CLI / Scripting
For developers, you can use the simulation engine directly:
```powershell
python example.py      # Rule-based simulation (Price controller)
python example_mpc.py  # Optimization-based simulation (MPC controller)
```

## Testing
Run the full test suite — covering physical energy conservation, financial E2E regressions, data-loader format detection, multi-battery comparison behavior, and the SlimmeMeterPortal API client:
```powershell
python -m pytest -s tests/
```
