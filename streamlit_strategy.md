# Strategy: BattWatt Web Interactive Tool

This document outlines the roadmap to transform the BattWatt simulation engine into a user-friendly web application using **Streamlit**.

## 1. Goal
Create an online dashboard where users can upload their Dutch P1 meter data (CSV) and receive an instant, professional evaluation of how different battery sizes and strategies would impact their energy bill.

## 2. Architecture for Web

### A. Data Handling (The "P1 Processor")
Since users have different formats (HomeWizard, Envergent, etc.), we need a robust ingestion layer.
- **Upload Component:** `st.file_uploader` for CSV (Meter data) and Excel (Market Prices).
- **Session State:** Store processed DataFrames in `st.session_state` to avoid re-processing on every UI interaction.
- **Privacy:** Implement a "No-Store" policy. Data is processed in RAM and deleted when the session ends.

### B. Interactive Simulation Sidebar
Allow users to play with variables without touching code:
- **Battery Specs:** Sliders for Capacity (kWh), Max Power (kW), and Efficiency (%).
- **Provider Settings:** Dropdown for common Dutch providers (Zonneplan, Tibber, Frank Energie) with pre-filled fees.
- **Strategy Toggle:** Select between "Self-Consumption (PV Priority)", "Price Arbitrage (Rule-based)", or "Cost Optimal (MPC)".
- **Tax Toggle:** Enable/Disable Net Metering (Saldering) to see future profitability.

### C. The Results Dashboard
Move beyond static Matplotlib windows to interactive Plotly charts:
- **Financial Summary:** Large "KPI Metrics" showing Total Savings, New Annual Bill, and Estimated Payback Period (ROI).
- **Energy Flow Graph:** An interactive zoomable chart showing SoC vs. Market Price.
- **Comparison View:** A side-by-side table comparing "No Battery" vs. "Simulated Battery".

## 3. Implementation Phases

### Phase 1: The "MVP" (Minimum Viable Product)
- Create `app.py`.
- Implement file upload for P1 CSV.
- Hardcode the 2025 Market Prices (or provide a default "Sample Data" button).
- Run the `Simulator` with the standard `Controller_PV`.
- Display a basic summary and the SoC plot.


## 2. Deployment Plan
- **Platform:** Streamlit Community Cloud (connected to GitLab).
- **Dependencies:** Add `streamlit`, `plotly`, `pyomo`, and `highspy` to `requirements.txt`.
- **URL:** Host at `https://battwatt.streamlit.app` (or similar).

## 3. Security & Safety Mandates
- **Anonymization:** Ensure the tool does not log user-uploaded energy data.
- **Solver Safety:** Use `highspy` as it is a thread-safe, memory-efficient solver suitable for shared cloud environments.
- **Input Validation:** Rigorously check CSV headers during upload to prevent app crashes.
