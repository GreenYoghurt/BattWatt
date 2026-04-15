# BattWatt

BattWatt is a Python-based tool designed to evaluate and simulate household energy consumption, solar PV production, and battery storage performance using Dutch day-ahead energy prices. It allows users to estimate potential savings by optimizing battery usage based on price fluctuations and solar availability.

## Features

- **Data Loading**: Import smart meter (P1) data and Entso-E day-ahead price data.
- **Battery Simulation**: Realistic battery modeling including capacity, charge/discharge limits, and efficiency losses.
- **Smart Controllers**:
  - **PV Controller**: Maximizes self-consumption of solar energy.
  - **Price Controller**: Optimizes battery charging/discharging based on rolling daily price quantiles to buy low and sell (or avoid buying) high.
- **Financial Analysis**: Calculate energy bills based on different Dutch energy provider models (e.g., Zonneplan, NextEnergy) including taxes, fees, and discounts.
- **Visualization**: Clear plots showing energy usage, prices, State of Charge (SoC), and the impact of the battery on grid interaction.

## Project Structure

- `battery.py`: Core battery logic and predefined battery profiles (e.g., Bliq, Victron).
- `controller_price.py`: Logic for price-aware battery management.
- `controller_PV.py`: Logic for PV-optimized battery management.
- `data_loader.py`: Utilities for reading CSV (P1 meter) and Excel (Price) data.
- `energy_providers.py`: Implementation of various energy provider pricing structures and Dutch energy taxes.
- `plotter.py`: Visualization functions using Matplotlib.
- `example.py`: A complete demonstration script.

## Installation

### Prerequisites
- Python 3.10 or higher

### Setup
1. Clone the repository to your local machine.
2. Create and activate a virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1  # Windows
   # source .venv/bin/activate   # Linux/macOS
   ```
3. Install the required dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

## Usage

1. **Prepare your data**:
   - Get your P1 meter data (CSV).
   - Get your day-ahead price data (Excel).
2. **Configure the script**:
   - Open `example.py` and update `PATH_PRICE` and `PATH_DATA` to point to your files.
3. **Run the simulation**:
   ```powershell
   python example.py
   ```

The script will output an energy balance check, calculate total costs before and after adding a battery, and display interactive plots.

## Example Output
The tool provides insights such as:
- Total energy cost without a battery.
- Total energy cost with a battery simulation.
- Net savings and battery efficiency (losses).
- Visual representation of the Battery State of Charge over time.

## License
This project is licensed under the [Creative Commons Attribution-NonCommercial 4.0 International](LICENSE) (CC BY-NC 4.0) license. 

This means you are free to share and adapt the material for non-commercial purposes, provided you give appropriate credit. Commercial use of this software is strictly prohibited. For more details, see the [official deed](https://creativecommons.org/licenses/by-nc/4.0/).
