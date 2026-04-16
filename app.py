import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from pathlib import Path
from data_loader import load_meter_data_HomeWizzard, load_price_data, merge_data
from energy_providers import get_providers
from battery import get_battery
from controllers.controller_PV import Controller_PV
from controllers.controller_price import Controller_price
from controllers.controller_MPC import Controller_MPC
from simulator import Simulator
from billing import BillingEngine
from models import SimulationResult

# Page configuration
st.set_page_config(page_title="BattWatt - Battery Evaluator", layout="wide")

st.title("🔋 BattWatt: Home Battery Evaluator")
st.markdown("""
Evaluate the impact of a home battery on your energy bill using Dutch market dynamics.
Upload your P1 meter data to get started.
""")

# Sidebar: Configuration
st.sidebar.header("1. Settings")

# Battery Selection
st.sidebar.subheader("Battery Configuration")
battery_options = ["Bliq_5kwh", "Bliq_10kwh", "Bliq_10kwh_fast", "Bliq_15kwh"]
selected_battery_name = st.sidebar.selectbox("Select a Battery Template", battery_options, index=1)
battery = get_battery(selected_battery_name)

# Provider Selection
st.sidebar.subheader("Energy Provider")
providers = get_providers()
selected_provider_name = st.sidebar.selectbox("Select your Provider", list(providers.keys()))
provider = providers[selected_provider_name]

# Strategy Selection
st.sidebar.subheader("Control Strategy")
strategy_map = {
    "PV Priority (Self-Consumption)": "PV",
    "Price Arbitrage (Rule-based)": "Price",
    "Cost Optimal (MPC)": "MPC"
}
selected_strategy = st.sidebar.selectbox("Select Strategy", list(strategy_map.keys()))

# Simulation Options
st.sidebar.subheader("Sim Options")
net_metering = st.sidebar.checkbox("Enable Net Metering (Saldering)", value=provider.net_metering)
provider.net_metering = net_metering

# File Uploaders
st.sidebar.header("2. Data Upload")
uploaded_meter = st.sidebar.file_uploader("Upload P1 Meter Data (HomeWizard CSV)", type=["csv"])
uploaded_price = st.sidebar.file_uploader("Upload Market Prices (ENTSO-E Excel)", type=["xlsx"])

# Sample Data Button (Optional helper)
if not uploaded_meter or not uploaded_price:
    st.info("Please upload your data files in the sidebar to run the simulation.")
    if st.button("Use Sample Data (2025 Placeholder)"):
        # This would require local files to exist, we'll assume they do for this MVP context
        # if Path("../example/day_ahead_2025.xlsx").exists(): ...
        st.warning("Sample data path not configured yet. Please upload files.")

if uploaded_meter and uploaded_price:
    with st.status("Processing data and running simulation...", expanded=True) as status:
        # Load Data
        st.write("Reading files...")
        # We need to handle file-like objects from Streamlit
        # For Excel we might need to save to a temp file or use BytesIO
        price_df = load_price_data(uploaded_price)
        meter_df = load_meter_data_HomeWizzard(uploaded_meter)
        
        st.write("Merging data...")
        merged_df = merge_data(meter_df, price_df)
        merged_df['day_ahead_price'] = merged_df['day_ahead_price']/1000 
        merged_df.set_index("timestamp", drop=False, inplace=True)
        
        st.write(f"Running {selected_strategy} simulation...")
        # Setup Controller
        if strategy_map[selected_strategy] == "PV":
            controller = Controller_PV(battery)
        elif strategy_map[selected_strategy] == "Price":
            controller = Controller_price(battery, merged_df)
        else: # MPC
            controller = Controller_MPC(battery, merged_df, provider, horizon_hours=24.0, reoptimize_every_hours=12.0)
            
        simulator = Simulator(battery, controller)
        result = simulator.run(merged_df)
        
        # Calculate Financials
        st.write("Calculating financials...")
        billing = BillingEngine(provider)
        
        baseline_result = SimulationResult(
            df=merged_df,
            total_production_kwh=merged_df['teruglevering'].sum(),
            total_consumption_kwh=merged_df['verbruik'].sum(),
            total_adjusted_production_kwh=merged_df['teruglevering'].sum(),
            total_adjusted_consumption_kwh=merged_df['verbruik'].sum(),
            final_soc_pct=0,
            final_soc_kwh=0,
            delta_soc_kwh=0
        )
        
        cost_baseline = billing.calculate_bill(baseline_result)
        cost_simulated = billing.calculate_bill(result)
        savings = cost_baseline - cost_simulated
        
        status.update(label="Simulation Complete!", state="complete", expanded=False)

    # Display Results
    st.header("Results Summary")
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Annual Bill (No Battery)", f"€{cost_baseline:.2f}")
    col2.metric("Annual Bill (With Battery)", f"€{cost_simulated:.2f}")
    col3.metric("Estimated Savings", f"€{savings:.2f}", delta=f"{savings:.2f}")

    # Charts
    st.subheader("Interactive Energy Flow")
    
    fig = go.Figure()
    # Market Price
    fig.add_trace(go.Scatter(x=result.df['timestamp'], y=result.df['day_ahead_price'], 
                             name="Market Price (€/kWh)", yaxis="y2", line=dict(color='rgba(200, 200, 200, 0.5)')))
    # Battery SoC
    fig.add_trace(go.Scatter(x=result.df['timestamp'], y=result.df['battery_soc'], 
                             name="Battery SoC (kWh)", fill='tozeroy', line=dict(color='green')))
    
    fig.update_layout(
        title="Battery State of Charge vs Market Price",
        xaxis_title="Time",
        yaxis=dict(title="SoC (kWh)", side="left"),
        yaxis2=dict(title="Price (€/kWh)", side="right", overlaying="y", showgrid=False),
        legend=dict(x=0, y=1.1, orientation="h")
    )
    
    st.plotly_chart(fig, use_container_width=True)

    # Data Table
    with st.expander("View Raw Simulation Data"):
        st.dataframe(result.df.head(100))
