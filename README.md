# BattWatt

BattWatt is a Python-based tool designed to evaluate and simulate household energy consumption, solar PV production, and battery storage performance using Dutch day-ahead energy prices. It allows users to estimate potential savings by optimizing battery usage based on price fluctuations and solar availability.

## Features

- **Interactive Web App**: User-friendly Streamlit interface for easy data upload and simulation.
- **Data Loading**: 
  - Import smart meter (P1) data from HomeWizard (CSV).
  - Automated Day-Ahead price fetching via **ENTSO-E API**.
  - Manual import support for ENTSO-E Excel exports.
- **Battery Simulation**: Realistic battery modeling including capacity, charge/discharge limits, and efficiency losses.
- **Advanced Controllers**:
  - **PV Controller**: Maximizes self-consumption of solar energy.
  - **Price Controller**: Rule-based optimization using price quantiles.
  - **MPC Controller**: Optimization-based (Model Predictive Control) using `Pyomo` and `HiGHS` solver for cost-optimal behavior.
- **Financial Analysis**: Comprehensive billing engine accounting for:
  - Dutch energy taxes and VAT (21%).
  - Net Metering (Salderingsregeling).
  - Custom provider fees (Subscription, Buying/Selling mark-ups).
- **Visualization**: Interactive Plotly charts and a visual progress bar for long-running simulations.

## Installation

### Prerequisites
- Python 3.12 or higher
- [HiGHS Solver](https://highs.dev/) (Recommended for MPC)

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
1. Configure your battery and provider settings in the sidebar.
2. Upload your HomeWizard P1 CSV export.
3. (Optional) Provide an ENTSO-E API key in `.streamlit/secrets.toml` for automated price fetching.
4. Click **🚀 Simuleer** and view results.

### CLI / Scripting
For developers, you can use the simulation engine directly:
```powershell
python example.py      # Rule-based simulation
python example_mpc.py  # Optimization-based simulation
```

## Testing
Run the suite of physical conservation and financial E2E tests:
```powershell
python -m pytest -s tests/
```
