import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from pathlib import Path
from data_loader import SmartLoader, load_price_data, fetch_entsoe_prices, merge_data
from energy_providers import get_providers, Provider
from battery import get_battery, Battery
from controllers import Controller_PV, Controller_price, Controller_MPC
from simulator import Simulator
from billing import BillingEngine
from models import SimulationResult
import base64

# Page configuration
st.set_page_config(page_title="BattWatt - Thuisbatterij Evaluator", layout="wide", page_icon="🔋")

# Helper to load images for CSS
def get_base64_of_bin_file(bin_file):
    with open(bin_file, 'rb') as f:
        data = f.read()
    return base64.b64encode(data).decode()

# Logo theme switching via CSS
try:
    logo_dark = get_base64_of_bin_file("assets/tudelft_logo.png")
    logo_light = get_base64_of_bin_file("assets/tudelft_logo_black.png")
    
    st.markdown(
        f"""
        <style>
        [data-testid="stSidebarNav"] {{
            padding-top: 20px;
        }}
        .logo-container {{
            text-align: center;
            padding: 10px;
        }}
        .logo-img {{
            width: 200px;
        }}
        @media (prefers-color-scheme: dark) {{
            .logo-light {{ display: none; }}
            .logo-dark {{ display: block; }}
        }}
        @media (prefers-color-scheme: light) {{
            .logo-light {{ display: block; }}
            .logo-dark {{ display: none; }}
        }}
        </style>
        """,
        unsafe_allow_html=True
    )
except Exception:
    pass

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
st.sidebar.header("1. Configuratie")

# Battery Selection
st.sidebar.subheader("Batterij")
battery_options = ["Bliq_5kwh", "Bliq_10kwh", "Bliq_10kwh_fast", "Bliq_15kwh"] + ["Handmatig invoeren (Custom)"]
selected_battery_name = st.sidebar.selectbox("Selecteer een batterij sjabloon", battery_options, index=1)

if selected_battery_name == "Handmatig invoeren (Custom)":
    with st.sidebar.expander("Batterij Details", expanded=True):
        custom_cap = st.number_input("Capaciteit (kWh)", value=10.0, step=0.5)
        custom_charge = st.number_input("Max. Laadvermogen (kW)", value=3.68, step=0.1)
        custom_discharge = st.number_input("Max. Ontlaadvermogen (kW)", value=3.68, step=0.1)
        custom_eff_charge = st.slider("Laadefficiëntie (%)", 80, 100, 98) / 100
        custom_eff_discharge = st.slider("Ontlaadefficiëntie (%)", 80, 100, 98) / 100
        
        battery = Battery(
            capacity_kwh=custom_cap,
            max_charge_kw=custom_charge,
            max_discharge_kw=custom_discharge,
            efficiency_charging=custom_eff_charge,
            efficiency_discharging=custom_eff_discharge
        )
else:
    battery = get_battery(selected_battery_name)

# Provider Selection
st.sidebar.subheader("Energieleverancier")
providers = get_providers()
provider_names = list(providers.keys()) + ["Handmatig invoeren (Custom)"]
selected_provider_name = st.sidebar.selectbox("Selecteer je leverancier", provider_names)

if selected_provider_name == "Handmatig invoeren (Custom)":
    with st.sidebar.expander("Provider Details", expanded=True):
        custom_name = st.text_input("Naam", value="Mijn Leverancier")
        custom_sub = st.number_input("Vaste leveringskosten (€/jaar)", value=75.0, step=1.0)
        custom_buy = st.number_input("Inkoop fee (€/kWh incl. BTW)", value=0.02, format="%.4f")
        custom_sell = st.number_input("Teruglever fee (€/kWh incl. BTW)", value=0.02, format="%.4f")
        custom_net = st.checkbox("Salderingsregeling (Net Metering)", value=True)
        
        provider = Provider(
            name=custom_name,
            subscription_cost=custom_sub,
            buying_fee=custom_buy,
            selling_fee=custom_sell,
            net_metering=custom_net,
            selling_fee_net_metering=True
        )
else:
    provider = providers[selected_provider_name]

# Strategy Selection
st.sidebar.subheader("Aansturing")
strategy_map = {
    "PV Prioriteit (Zelfconsumptie)": "PV",
    "Prijs Arbitrage (Regelgebaseerd)": "Price",
    "Kosten Optimaal (MPC)": "MPC"
}
selected_strategy = st.sidebar.selectbox("Selecteer Strategie", list(strategy_map.keys()))

# Simulation Options
net_metering = st.sidebar.toggle("Salderingsregeling toepassen", value=provider.net_metering)
provider.net_metering = net_metering

st.sidebar.divider()

# File Uploaders
st.sidebar.header("2. Data Input")

st.sidebar.subheader("Marktprijzen")
price_source = st.sidebar.radio("Bron marktprijzen", ["Automatisch (ENTSO-E API)", "Handmatig uploaden (.xlsx)"])
uploaded_price = None
if price_source == "Handmatig uploaden (.xlsx)":
    uploaded_price = st.sidebar.file_uploader("Upload Marktprijzen (ENTSO-E Excel)", type=["xlsx"])

st.sidebar.subheader("Meter Data")
uploaded_meter = st.sidebar.file_uploader("Upload Meter Data (CSV of Excel)", type=["csv", "xlsx"])

with st.sidebar.expander("ℹ️ Ondersteunde Formaten"):
    st.markdown("""
    **Automatisch Herkend:**
    - HomeWizard CSV (Export uit app)
    - Standaard DSO Excel (datum_tijd, levering_normaal, etc.)
    
    **Ander formaat?** Gebruik de 'Aangepaste Mapping' hieronder.
    """)
with st.sidebar.expander("📝 Aangepaste Mapping", expanded=False):
    st.info("Alleen nodig als je bestand niet automatisch wordt herkend.")
    use_custom_mapping = st.checkbox("Gebruik handmatige mapping", value=False)
    fmt = st.selectbox("Bestandstype", ["csv", "excel"], index=0)
    sep = st.text_input("Scheidingsteken (alleen CSV)", value=";")
    dec = st.text_input("Decimaalteken", value=",")
    col_time = st.text_input("Kolomnaam Tijdstip", value="datum_tijd")
    col_imp = st.text_input("Kolomnaam Verbruik/Import", value="verbruik")
    col_exp = st.text_input("Kolomnaam Teruglevering/Export", value="teruglevering")
    is_cum = st.checkbox("Meterstanden zijn cumulatief", value=False)

    custom_mapping = None
    if use_custom_mapping:
        custom_mapping = {
            "format": fmt,
            "delimiter": sep,
            "decimal": dec,
            "columns": {
                "timestamp": col_time,
                "import": col_imp,
                "export": col_exp
            },
            "is_cumulative": is_cum
        }

st.sidebar.divider()

# Simulation Button
can_simulate = False
if uploaded_meter:
    can_simulate = True
    if price_source == "Automatisch (ENTSO-E API)" and not ENTSOE_API_KEY:
        st.sidebar.error("⚠️ Geen API Key geconfigureerd.")
        can_simulate = False
    elif price_source == "Handmatig uploaden (.xlsx)" and not uploaded_price:
        can_simulate = False

if st.sidebar.button("🚀 Start Simulatie", use_container_width=True, type="primary", disabled=not can_simulate):
    with st.status("Data verwerken en simulatie uitvoeren...", expanded=True) as status:
        # 1. Load Meter Data
        st.write("Meterdata inlezen...")
        try:
            meter_df = SmartLoader.load(uploaded_meter, config=custom_mapping)
        except Exception as e:
            st.error(f"Fout bij inlezen meterdata: {e}")
            st.stop()
        
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

        # Store in session state for persistence between re-runs
        st.session_state['simulation_result'] = {
            'result': result,
            'cost_baseline': cost_baseline,
            'cost_simulated': cost_simulated,
            'savings': savings,
            'strategy': strategy_map[selected_strategy]
        }

# Credits & Logo
st.sidebar.markdown("---")
try:
    st.sidebar.markdown(
        f"""
        <div class="logo-container">
            <img class="logo-img logo-dark" src="data:image/png;base64,{logo_dark}">
            <img class="logo-img logo-light" src="data:image/png;base64,{logo_light}">
        </div>
        """,
        unsafe_allow_html=True
    )
except Exception:
    st.sidebar.image("assets/tudelft_logo.png", width=250)

st.sidebar.markdown("**Ontwikkeld door:**  \n[Jort Groen](https://github.com/JortGroen)\n[Brecht Goethals](https://github.com/Brecht1949)")
st.sidebar.caption("Technische Universiteit Delft")

# Main Area Display
if 'simulation_result' in st.session_state:
    res_data = st.session_state['simulation_result']
    result = res_data['result']
    cost_baseline = res_data['cost_baseline']
    cost_simulated = res_data['cost_simulated']
    savings = res_data['savings']
    strategy = res_data.get('strategy', 'PV')

    # Display Results
    st.header("Resultaten Overzicht")
    
    # Check if we need a 4th column for realistic MPC savings
    if strategy == "MPC":
        col1, col2, col3, col4 = st.columns(4)
        realistic_savings = savings * 0.8
        col4.metric("Realistische Besparing (80%)", f"€{realistic_savings:.2f}", 
                   help="In de werkelijkheid kan een algoritme nooit een perfecte voorspelling doen van het energieverbruik en de zonne-opbrengst. Deze waarde geeft een realistischer beeld van de te verwachten besparing.")
    else:
        col1, col2, col3 = st.columns(3)

    col1.metric("Jaarnota (Zonder Batterij)", f"€{cost_baseline:.2f}")
    col2.metric("Jaarnota (Met Batterij)", f"€{cost_simulated:.2f}")
    col3.metric("Geschatte Besparing", f"€{savings:.2f}", delta=f"{savings:.2f}")

    st.caption("⚠️ **Let op:** Deze waarden zijn schattingen gebaseerd op historische data en simulatiemodellen. De werkelijke resultaten kunnen afwijken door o.a. weersomstandigheden, batterij-degradatie en wijzigingen in markttarieven. Gebruik deze resultaten enkel ter oriëntatie.")

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
elif not uploaded_meter:
    st.info("👈 Upload je P1-metergegevens in de zijbalk om de berekening te starten.")
