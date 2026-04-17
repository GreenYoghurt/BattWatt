import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from pathlib import Path
from data_loader import load_meter_data_HomeWizzard, load_price_data, fetch_entsoe_prices, merge_data
from energy_providers import get_providers
from battery import get_battery
from controllers import Controller_PV, Controller_price, Controller_MPC
from simulator import Simulator
from billing import BillingEngine
from models import SimulationResult

# Page configuration
st.set_page_config(page_title="BattWatt - Batterij Evaluator", layout="wide")

# Handle Secrets / API Key
try:
    ENTSOE_API_KEY = st.secrets["ENTSOE_API_KEY"]
except Exception:
    ENTSOE_API_KEY = None

st.title("🔋 BattWatt: Thuisbatterij Evaluator")
st.markdown("""
Evalueer de impact van een thuisbatterij op je energierekening met de Nederlandse marktdynamiek.
Upload je P1-metergegevens om te beginnen.
""")

# Sidebar: Configuration
st.sidebar.header("1. Instellingen")

# Battery Selection
st.sidebar.subheader("Batterij Configuratie")
battery_options = ["Bliq_5kwh", "Bliq_10kwh", "Bliq_10kwh_fast", "Bliq_15kwh"]
selected_battery_name = st.sidebar.selectbox("Selecteer een batterij sjabloon", battery_options, index=1)
battery = get_battery(selected_battery_name)

# Provider Selection
st.sidebar.subheader("Energieleverancier")
providers = get_providers()
selected_provider_name = st.sidebar.selectbox("Selecteer je leverancier", list(providers.keys()))
provider = providers[selected_provider_name]

# Strategy Selection
st.sidebar.subheader("Aansturingsstrategie")
strategy_map = {
    "PV Prioriteit (Zelfconsumptie)": "PV",
    "Prijs Arbitrage (Regelgebaseerd)": "Price",
    "Kosten Optimaal (MPC)": "MPC"
}
selected_strategy = st.sidebar.selectbox("Selecteer Strategie", list(strategy_map.keys()))

# Simulation Options
st.sidebar.subheader("Simulatie Opties")
net_metering = st.sidebar.checkbox("Salderingsregeling inschakelen", value=provider.net_metering)
provider.net_metering = net_metering

# File Uploaders
st.sidebar.header("2. Data Upload")
uploaded_meter = st.sidebar.file_uploader("Upload P1 Meter Data (HomeWizard CSV)", type=["csv"])

st.sidebar.subheader("Marktprijzen")
price_source = st.sidebar.radio("Bron marktprijzen", ["Automatisch (ENTSO-E API)", "Handmatig uploaden (.xlsx)"])
uploaded_price = None
if price_source == "Handmatig uploaden (.xlsx)":
    uploaded_price = st.sidebar.file_uploader("Upload Marktprijzen (ENTSO-E Excel)", type=["xlsx"])

# Credits & Logo
st.sidebar.markdown("---")
st.sidebar.image("assets/tudelft_logo.png", width=250)
st.sidebar.markdown("**Ontwikkeld door:**  \n[Jort Groen](https://github.com/JortGroen)")
st.sidebar.caption("Technische Universiteit Delft")

if uploaded_meter:
    # Check if we have everything needed to simulate
    can_simulate = True
    if price_source == "Automatisch (ENTSO-E API)" and not ENTSOE_API_KEY:
        st.sidebar.error("⚠️ Geen API Key geconfigureerd.")
        can_simulate = False
    elif price_source == "Handmatig uploaden (.xlsx)" and not uploaded_price:
        can_simulate = False

    if st.sidebar.button("🚀 Simuleer", use_container_width=True, disabled=not can_simulate):
        with st.status("Data verwerken en simulatie uitvoeren...", expanded=True) as status:
            # 1. Load Meter Data
            st.write("Meterdata inlezen...")
            meter_df = load_meter_data_HomeWizzard(uploaded_meter)
            
            # 2. Get Price Data
            if price_source == "Automatisch (ENTSO-E API)":
                start_date = meter_df['timestamp'].min()
                end_date = meter_df['timestamp'].max()
                st.write(f"Marktprijzen ophalen via API ({start_date.date()} tot {end_date.date()})...")
                try:
                    price_df = fetch_entsoe_prices(ENTSOE_API_KEY, start_date, end_date)
                except Exception as e:
                    st.error(f"Fout bij ophalen prijzen: {e}")
                    st.stop()
            else:
                st.write("Marktprijzen inlezen uit bestand...")
                price_df = load_price_data(uploaded_price)
            
            # 3. Merge Data
            st.write("Data samenvoegen...")
            merged_df = merge_data(meter_df, price_df)
            merged_df['day_ahead_price'] = merged_df['day_ahead_price']/1000 
            merged_df.set_index("timestamp", drop=False, inplace=True)
            
            st.write(f"Uitvoeren van {selected_strategy} simulatie...")
            # 4. Setup Controller & Run Simulation
            if strategy_map[selected_strategy] == "PV":
                controller = Controller_PV(battery)
            elif strategy_map[selected_strategy] == "Price":
                controller = Controller_price(battery, merged_df)
            else: # MPC
                controller = Controller_MPC(battery, merged_df, provider, horizon_hours=24.0, reoptimize_every_hours=12.0)
                
            simulator = Simulator(battery, controller)
            
            # Progress bar for the simulation
            progress_bar = st.progress(0, text="Simulatie voortgang")
            def update_progress(current, total):
                progress_bar.progress(current / total, text=f"Simulatie voortgang: {current}/{total} stappen")
            
            result = simulator.run(merged_df, progress_callback=update_progress)
            progress_bar.empty()
            
            # 5. Calculate Financials
            st.write("Financiële berekeningen uitvoeren...")
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
            
            status.update(label="Simulatie Voltooid!", state="complete", expanded=False)

        # Display Results
        st.header("Resultaten Overzicht")
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Jaarnota (Zonder Batterij)", f"€{cost_baseline:.2f}")
        col2.metric("Jaarnota (Met Batterij)", f"€{cost_simulated:.2f}")
        col3.metric("Geschatte Besparing", f"€{savings:.2f}", delta=f"{savings:.2f}")

        # Charts
        st.subheader("Interactieve Energieflow")
        
        fig = go.Figure()
        # Market Price
        fig.add_trace(go.Scatter(x=result.df['timestamp'], y=result.df['day_ahead_price'], 
                                 name="Marktprijs (€/kWh)", yaxis="y2", line=dict(color='rgba(200, 200, 200, 0.5)')))
        # Battery SoC
        fig.add_trace(go.Scatter(x=result.df['timestamp'], y=result.df['battery_soc'], 
                                 name="Batterij SoC (kWh)", fill='tozeroy', line=dict(color='green')))
        
        fig.update_layout(
            title="Batterij Laadtoestand (SoC) vs Marktprijs",
            xaxis_title="Tijd",
            yaxis=dict(title="SoC (kWh)", side="left"),
            yaxis2=dict(title="Prijs (€/kWh)", side="right", overlaying="y", showgrid=False),
            legend=dict(x=0, y=1.1, orientation="h")
        )
        
        st.plotly_chart(fig, use_container_width=True)

        # Data Table
        with st.expander("Bekijk Ruwe Simulatiedata"):
            st.dataframe(result.df.head(100))
else:
    st.info("Upload je P1-metergegevens in de zijbalk om de berekening te starten.")
