import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from pathlib import Path
from data_loader import SmartLoader, load_price_data, fetch_entsoe_prices, merge_data
from energy_providers import get_providers, Provider
from battery import get_battery, Battery
from controllers import Controller_PV, Controller_MPC
from simulator import Simulator
from billing import BillingEngine
from models import SimulationResult
import base64

# Page configuration
st.set_page_config(page_title="BattWatt - Thuisbatterij Evaluator", layout="wide", page_icon="🔋")

# Helper to load images for CSS
def get_base64_of_bin_file(bin_file):
    try:
        with open(bin_file, 'rb') as f:
            data = f.read()
        return base64.b64encode(data).decode()
    except:
        return ""

# Logo theme switching via CSS
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

def get_duration_conv(df):
    if len(df) > 1:
        duration_hours = (df['timestamp'].iloc[1] - df['timestamp'].iloc[0]).total_seconds() / 3600
        if duration_hours <= 0: duration_hours = 0.25
    else:
        duration_hours = 0.25
    return 1.0 / duration_hours

def create_usage_chart(df, title="Verbruik vs Batterij Status"):
    conv = get_duration_conv(df)
    fig = go.Figure()
    
    # Battery SoC (%) - Secondary Axis
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df['battery_soc'], 
                                 name="Batterij SoC (%)", fill='tozeroy', 
                                 line=dict(color='rgba(0, 128, 0, 0.2)', width=0),
                                 yaxis="y2"))
    
    # PV (Zon-opbrengst in kW) - Primary Axis
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df['teruglevering'] * conv, 
                                 name="Zon-opbrengst (kW)", 
                                 line=dict(color='orange', width=2)))
    
    # Load (Huisverbruik in kW) - Primary Axis
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df['verbruik'] * conv, 
                                 name="Huisverbruik (kW)", 
                                 line=dict(color='red', width=1.5)))
    
    fig.update_layout(
        title=title,
        xaxis_title="Tijd",
        yaxis=dict(title="Vermogen (kW)", side="left"),
        yaxis2=dict(title="Batterij SoC (%)", side="right", overlaying="y", showgrid=False, range=[0, 105]),
        legend=dict(x=0, y=1.1, orientation="h"),
        height=400,
        hovermode="x unified"
    )
    return fig

def create_price_chart(df, title="Marktprijs vs Batterij SoC"):
    fig = go.Figure()
    
    # Market Price (€/kWh) - Secondary Axis
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df['day_ahead_price'], 
                                 name="Marktprijs (€/kWh)", yaxis="y2", 
                                 line=dict(color='rgba(200, 200, 200, 0.8)', width=2)))
    
    # Battery SoC (kWh) - Primary Axis
    soc_values = df.get('battery_soc_kwh', df['battery_soc']) # Fallback if kwh not calc yet
    fig.add_trace(go.Scatter(x=df['timestamp'], y=soc_values, 
                                 name="Batterij SoC (kWh)", fill='tozeroy', 
                                 line=dict(color='green', width=2)))
    
    fig.update_layout(
        title=title,
        xaxis_title="Tijd",
        yaxis=dict(title="SoC (kWh)", side="left"),
        yaxis2=dict(title="Prijs (€/kWh)", side="right", overlaying="y", showgrid=False),
        legend=dict(x=0, y=1.1, orientation="h"),
        height=400,
        hovermode="x unified"
    )
    return fig

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
battery_options = ["Geen batterij (Baseline)"] + ["Bliq_5kwh", "Bliq_10kwh", "Bliq_10kwh_fast", "Bliq_15kwh"] + ["Handmatig invoeren (Custom)"]
selected_battery_name = st.sidebar.selectbox("Selecteer een batterij sjabloon", battery_options, index=2) # Default to Bliq_10kwh

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
elif selected_battery_name == "Geen batterij (Baseline)":
    battery = Battery(capacity_kwh=0, max_charge_kw=0, max_discharge_kw=0)
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
    "Kosten Optimaal (MPC)": "MPC"
}
selected_strategy = st.sidebar.selectbox("Selecteer Strategie", list(strategy_map.keys()))

# Simulation Options
# net_metering = st.sidebar.toggle("Salderingsregeling toepassen", value=provider.net_metering)
net_metering = False # User request: hide but keep logic. Default to False for battery evaluation.
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

def _run_simulation(meter_df):
    """Load prices, run simulation, store results in session_state."""
    with st.status("Data verwerken en simulatie uitvoeren...", expanded=True) as status:
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
        else:  # MPC
            controller = Controller_MPC(battery, merged_df, provider, horizon_hours=24.0, reoptimize_every_hours=12.0)

        simulator = Simulator(battery, controller)

        progress_bar = st.progress(0, text="Simulatie voortgang")
        def update_progress(current, total):
            progress_bar.progress(current / total, text=f"Simulatie voortgang: {current}/{total} stappen")

        result = simulator.run(merged_df, progress_callback=update_progress)
        progress_bar.empty()

        result.df['battery_soc_kwh'] = result.df['battery_soc'] * battery.capacity_kwh / 100

        # 5. Calculate Financials
        st.write("Financiële berekeningen uitvoeren...")
        billing = BillingEngine(provider)

        # Baseline: net verbruik and teruglevering per interval so billing uses the
        # same netting logic as the simulated result. Without this, intervals where
        # both import and export are non-zero (possible in real meter data due to
        # phase imbalance or tariff transitions) would generate phantom savings
        # unrelated to the battery.
        baseline_df = merged_df.copy()
        net = baseline_df['teruglevering'] - baseline_df['verbruik']
        baseline_df['adjusted_consumption'] = (-net).clip(lower=0)
        baseline_df['adjusted_production'] = net.clip(lower=0)

        baseline_result = SimulationResult(
            df=baseline_df,
            total_production_kwh=merged_df['teruglevering'].sum(),
            total_consumption_kwh=merged_df['verbruik'].sum(),
            total_adjusted_production_kwh=baseline_df['adjusted_production'].sum(),
            total_adjusted_consumption_kwh=baseline_df['adjusted_consumption'].sum(),
            final_soc_pct=0,
            final_soc_kwh=0,
            delta_soc_kwh=0
        )

        cost_baseline = billing.calculate_bill(baseline_result)
        cost_simulated = billing.calculate_bill(result)
        savings = cost_baseline - cost_simulated

        breakdown_baseline = billing.calculate_bill_breakdown(baseline_result)
        breakdown_simulated = billing.calculate_bill_breakdown(result)

        status.update(label="Simulatie Voltooid!", state="complete", expanded=False)
        st.session_state['simulation_result'] = {
            'result': result,
            'cost_baseline': cost_baseline,
            'cost_simulated': cost_simulated,
            'savings': savings,
            'strategy': strategy_map[selected_strategy],
            'breakdown_baseline': breakdown_baseline,
            'breakdown_simulated': breakdown_simulated,
        }


if st.sidebar.button("🚀 Start Simulatie", use_container_width=True, type="primary", disabled=not can_simulate):
    # 1. Load Meter Data & run quality checks
    try:
        meter_df, data_checks = SmartLoader.load_with_checks(uploaded_meter, config=custom_mapping)
    except Exception as e:
        st.error(f"Fout bij inlezen meterdata: {e}")
        st.stop()

    failed_checks = [c for c in data_checks if not c.passed]
    if failed_checks:
        st.session_state['pending_simulation'] = {
            'meter_df': meter_df,
            'failed_checks': failed_checks,
        }
        st.session_state.pop('simulation_result', None)
    else:
        st.session_state.pop('pending_simulation', None)
        _run_simulation(meter_df)

# Credits & Logo
st.sidebar.markdown("---")
if logo_dark and logo_light:
    st.sidebar.markdown(
        f"""
        <div class="logo-container">
            <img class="logo-img logo-dark" src="data:image/png;base64,{logo_dark}">
            <img class="logo-img logo-light" src="data:image/png;base64,{logo_light}">
        </div>
        """,
        unsafe_allow_html=True
    )
else:
    st.sidebar.image("assets/tudelft_logo.png", width=250)

st.sidebar.markdown("**Ontwikkeld door:**  \n[Jort Groen](https://www.linkedin.com/in/jortgroen/)\n[Brecht Goethals](https://github.com/Brecht1949)")
st.sidebar.caption("Technische Universiteit Delft")

# Main Area Display
if 'pending_simulation' in st.session_state:
    pending = st.session_state['pending_simulation']
    for check in pending['failed_checks']:
        st.warning(f"**{check.title}**\n\n{check.message}")
    st.divider()
    if st.button("▶ Analyse alsnog uitvoeren", type="primary"):
        meter_df = st.session_state['pending_simulation']['meter_df']
        del st.session_state['pending_simulation']
        _run_simulation(meter_df)
        st.rerun()

elif 'simulation_result' in st.session_state:
    res_data = st.session_state['simulation_result']
    result = res_data['result']
    cost_baseline = res_data['cost_baseline']
    cost_simulated = res_data['cost_simulated']
    savings = res_data['savings']
    strategy = res_data.get('strategy', 'PV')

    # Display Results
    st.header("Resultaten Overzicht")
    include_fixed = st.checkbox("Vaste kosten meenemen", value=True,
                                help="Vaste kosten omvatten abonnementskosten, netbeheerskosten en belastingvermindering. Zet uit om alleen het variabele deel te vergelijken.")

    breakdown_baseline = res_data.get('breakdown_baseline')
    breakdown_simulated = res_data.get('breakdown_simulated')

    if not include_fixed and breakdown_baseline and breakdown_simulated:
        fixed_base = breakdown_baseline['abonnementskosten'] + breakdown_baseline['netbeheerskosten'] - breakdown_baseline['belastingvermindering']
        fixed_sim  = breakdown_simulated['abonnementskosten'] + breakdown_simulated['netbeheerskosten'] - breakdown_simulated['belastingvermindering']
        display_baseline = cost_baseline - fixed_base
        display_simulated = cost_simulated - fixed_sim
    else:
        display_baseline = cost_baseline
        display_simulated = cost_simulated
    display_savings = display_baseline - display_simulated

    # Check if we need a 4th column for realistic MPC savings
    if strategy == "MPC":
        col1, col2, col3, col4, col5 = st.columns(5)
        realistic_savings = display_savings * 0.8

        col1.metric("Jaarnota (Zonder Batterij)", f"€{display_baseline:.2f}")
        col2.metric("Jaarnota (Met Batterij)", f"€{display_simulated:.2f}")
        col3.metric("Geschatte Besparing", f"€{display_savings:.2f}", delta=f"{display_savings:.2f}")
        col4.metric("Realistische Besparing (80%)", f"€{realistic_savings:.2f}",
                   help="In de werkelijkheid kan een algoritme nooit een perfecte voorspelling doen van het energieverbruik en de zonne-opbrengst. Deze waarde geeft een realistischer beeld van de te verwachten besparing.")
        col5.metric("Batterij Cycli 🔄", f"{getattr(result, 'total_cycles', 0.0):.1f}")
    else:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Jaarnota (Zonder Batterij)", f"€{display_baseline:.2f}")
        col2.metric("Jaarnota (Met Batterij)", f"€{display_simulated:.2f}")
        col3.metric("Geschatte Besparing", f"€{display_savings:.2f}", delta=f"{display_savings:.2f}")
        col4.metric("Batterij Cycli 🔄", f"{getattr(result, 'total_cycles', 0.0):.1f}")

    st.caption("⚠️ **Let op:** Deze waarden zijn schattingen gebaseerd op historische data en simulatiemodellen. De werkelijke resultaten kunnen afwijken door o.a. weersomstandigheden, batterij-degradatie en wijzigingen in markttarieven. Gebruik deze resultaten enkel ter oriëntatie.")

    # Cost breakdown table
    if breakdown_baseline and breakdown_simulated:
        with st.expander("Kostenopbouw", expanded=True):
            bd = breakdown_baseline  # rates are provider-level, same for both

            def avg_per_kwh(cost_key, vol_key):
                vol = bd[vol_key]
                return bd[cost_key] / vol if vol else 0.0

            # (label, cost_key, is_credit, volume_key, is_fixed, tarief_str)
            all_rows = [
                ("Abonnementskosten",               "abonnementskosten",         False, None,                    True,
                 f"€ {bd['tarief_abonnementskosten']:.2f}/jaar"),
                ("Netbeheerskosten",                 "netbeheerskosten",           False, None,                    True,
                 f"€ {bd['tarief_netbeheerskosten']:.2f}/jaar"),
                ("Belastingvermindering",            "belastingvermindering",      True,  None,                    True,
                 f"€ {bd['tarief_belastingvermindering']:.2f}/jaar"),
                ("Marktprijs inkoop",                "marktprijs_inkoop",          False, "total_consumption_kwh", False,
                 f"gem. € {avg_per_kwh('marktprijs_inkoop', 'total_consumption_kwh'):.4f}/kWh"),
                ("Energiebelasting",                 "energiebelasting",           False, "energiebelasting_kwh",  False,
                 f"€ {bd['tarief_energiebelasting_per_kwh']:.4f}/kWh"),
                ("Leveranciersopslag inkoop",        "leveranciersopslag_inkoop",  False, "total_consumption_kwh", False,
                 f"€ {bd['tarief_leveranciersopslag_inkoop_per_kwh']:.4f}/kWh"),
                ("Leveranciersopslag teruglevering", "leveranciersopslag_verkoop", False, "total_feed_in_kwh",     False,
                 f"€ {bd['tarief_leveranciersopslag_verkoop_per_kwh']:.4f}/kWh"),
                ("Teruglevering opbrengst",          "teruglevering_opbrengst",    True,  "total_feed_in_kwh",     False,
                 f"gem. € {avg_per_kwh('teruglevering_opbrengst', 'total_feed_in_kwh'):.4f}/kWh"),
            ]
            rows = [r for r in all_rows if include_fixed or not r[4]]

            col_label, col_base, col_sim, col_diff = st.columns([3, 2, 2, 2])
            col_label.markdown("**Post**")
            col_base.markdown("**Zonder batterij**")
            col_sim.markdown("**Met batterij**")
            col_diff.markdown("**Verschil**")

            st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)

            def fmt_cell(amount, volume_kwh):
                sign = "−" if amount < 0 else ""
                vol_html = f" <small style='color:grey'>({volume_kwh:,.0f} kWh)</small>" if volume_kwh is not None else ""
                return f"{sign}€ {abs(amount):,.2f}{vol_html}"

            prev_fixed = None
            for label, key, is_credit, vol_key, is_fixed, tarief_str in rows:
                # Section header when switching between fixed and variable
                if include_fixed and prev_fixed is not None and prev_fixed != is_fixed:
                    st.markdown("<div style='margin:6px 0 2px 0; font-size:0.75rem; color:grey; text-transform:uppercase; letter-spacing:0.05em'>Variabele kosten</div>", unsafe_allow_html=True)
                elif include_fixed and prev_fixed is None:
                    st.markdown("<div style='margin:0 0 2px 0; font-size:0.75rem; color:grey; text-transform:uppercase; letter-spacing:0.05em'>Vaste kosten</div>", unsafe_allow_html=True)
                prev_fixed = is_fixed

                raw_base = breakdown_baseline[key]
                raw_sim = breakdown_simulated[key]
                base_contribution = -raw_base if is_credit else raw_base
                sim_contribution = -raw_sim if is_credit else raw_sim
                diff = base_contribution - sim_contribution

                base_vol = breakdown_baseline[vol_key] if vol_key else None
                sim_vol = breakdown_simulated[vol_key] if vol_key else None

                col_label, col_base, col_sim, col_diff = st.columns([3, 2, 2, 2])
                col_label.markdown(
                    f"{label} <small style='color:grey'>({tarief_str})</small>",
                    unsafe_allow_html=True
                )
                col_base.markdown(fmt_cell(base_contribution, base_vol), unsafe_allow_html=True)
                col_sim.markdown(fmt_cell(sim_contribution, sim_vol), unsafe_allow_html=True)
                if abs(diff) < 0.005:
                    col_diff.write("—")
                elif diff > 0:
                    col_diff.markdown(f"<span style='color:green'>▼ € {diff:,.2f}</span>", unsafe_allow_html=True)
                else:
                    col_diff.markdown(f"<span style='color:red'>▲ € {abs(diff):,.2f}</span>", unsafe_allow_html=True)

            st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)
            col_label, col_base, col_sim, col_diff = st.columns([3, 2, 2, 2])
            total_diff = display_baseline - display_simulated
            col_label.markdown("**Totaal**")
            col_base.markdown(f"**€ {display_baseline:,.2f}**")
            col_sim.markdown(f"**€ {display_simulated:,.2f}**")
            if total_diff > 0:
                col_diff.markdown(f"<span style='color:green'>**▼ € {total_diff:,.2f}**</span>", unsafe_allow_html=True)
            else:
                col_diff.markdown(f"<span style='color:red'>**▲ € {abs(total_diff):,.2f}**</span>", unsafe_allow_html=True)

    # Charts
    with st.expander("Interactieve Energieflow", expanded=True):
        duration = result.df['timestamp'].max() - result.df['timestamp'].min()

        if duration > pd.Timedelta(days=32):
            st.info("De simulatie beslaat een lange periode. Hieronder zie je representatieve weken voor verschillende seizoenen en het totaaloverzicht.")

            seasons = {
                "❄️ Winter (Jan)": 1,
                "🌱 Lente (Apr)": 4,
                "☀️ Zomer (Jul)": 7,
                "🍂 Herfst (Okt)": 10
            }

            available_seasons = {}
            for name, month in seasons.items():
                mask = result.df['timestamp'].dt.month == month
                if mask.any():
                    start_time = result.df[mask]['timestamp'].min()
                    end_time = start_time + pd.Timedelta(days=7)
                    available_seasons[name] = result.df[(result.df['timestamp'] >= start_time) & (result.df['timestamp'] < end_time)]

            tab_names = ["📊 Volledige Periode"] + list(available_seasons.keys())
            if not available_seasons:
                tab_names += ["Begin van periode", "Einde van periode"]

            tabs = st.tabs(tab_names)

            for i, t_name in enumerate(tab_names):
                with tabs[i]:
                    if i == 0:
                        plot_df = result.df
                        slice_title = "Volledige Periode"
                    else:
                        s_name = list(available_seasons.keys())[i-1] if available_seasons else (["Begin van periode", "Einde van periode"][i-1])
                        plot_df = available_seasons[s_name] if available_seasons else (result.df.head(7*24*4) if i==1 else result.df.tail(7*24*4))
                        slice_title = s_name

                    st.plotly_chart(create_usage_chart(plot_df, title=f"Huisverbruik & Zon vs Batterij Status (%) - {slice_title}"), use_container_width=True)
                    st.plotly_chart(create_price_chart(plot_df, title=f"Marktprijs vs Batterij SoC (kWh) - {slice_title}"), use_container_width=True)
        else:
            st.plotly_chart(create_usage_chart(result.df, title="Huisverbruik & Zon vs Batterij Status (%)"), use_container_width=True)
            st.plotly_chart(create_price_chart(result.df, title="Marktprijs vs Batterij SoC (kWh)"), use_container_width=True)

    # Data Table
    with st.expander("Bekijk Ruwe Simulatiedata"):
        st.dataframe(result.df.head(100))
elif not uploaded_meter:
    st.info("👈 Upload je P1-metergegevens in de zijbalk om de berekening te starten.")
