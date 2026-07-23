import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from pathlib import Path
from data_loader import SmartLoader, load_price_data, fetch_entsoe_prices, merge_data, SlimmeMeterPortalAPILoader
from slimmemeterportal_client import SlimmeMeterPortalClient, SlimmeMeterPortalError, flatten_usage_range, find_missing_dates
from energy_providers import Provider
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
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df['battery_soc'],
                             name="Batterij SoC (%)", fill='tozeroy',
                             line=dict(color='rgba(0, 128, 0, 0.2)', width=0),
                             yaxis="y2"))
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df['teruglevering'] * conv,
                             name="Zon-opbrengst (kW)",
                             line=dict(color='orange', width=2)))
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
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df['day_ahead_price'],
                             name="Marktprijs (€/kWh)", yaxis="y2",
                             line=dict(color='rgba(200, 200, 200, 0.8)', width=2)))
    soc_values = df.get('battery_soc_kwh', df['battery_soc'])
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

# ── Battery configuration helpers ────────────────────────────────────────────

_battery_display_to_key = {
    "5 kWh": "Bliq_5kwh",
    "10 kWh": "Bliq_10kwh",
    "10 kWh (snel laden)": "Bliq_10kwh_fast",
    "15 kWh": "Bliq_15kwh",
}
_battery_preset_options = list(_battery_display_to_key.keys()) + ["Handmatig invoeren (Custom)"]

def _battery_from_id(bid):
    preset = st.session_state.get(f"bat_preset_{bid}", "10 kWh")
    if preset == "Handmatig invoeren (Custom)":
        return Battery(
            capacity_kwh=st.session_state.get(f"bat_cap_{bid}", 10.0),
            max_charge_kw=st.session_state.get(f"bat_charge_{bid}", 3.68),
            max_discharge_kw=st.session_state.get(f"bat_discharge_{bid}", 3.68),
            efficiency_charging=st.session_state.get(f"bat_eff_c_{bid}", 98) / 100,
            efficiency_discharging=st.session_state.get(f"bat_eff_d_{bid}", 98) / 100,
        )
    return get_battery(_battery_display_to_key[preset])

# ── Unified breakdown comparison table ────────────────────────────────────────

def _render_unified_breakdown(all_results, display_baseline, display_costs, breakdown_baseline, include_fixed):
    bd = breakdown_baseline

    def avg_per_kwh(breakdown, cost_key, vol_key):
        vol = breakdown[vol_key]
        return breakdown[cost_key] / vol if vol else 0.0

    all_rows_def = [
        ("Abonnementskosten",               "abonnementskosten",         False, None,                    True,
         f"€ {bd['tarief_abonnementskosten']:.2f}/jaar"),
        ("Netbeheerskosten",                 "netbeheerskosten",           False, None,                    True,
         f"€ {bd['tarief_netbeheerskosten']:.2f}/jaar"),
        ("Belastingvermindering",            "belastingvermindering",      True,  None,                    True,
         f"€ {bd['tarief_belastingvermindering']:.2f}/jaar"),
        ("Marktprijs inkoop",                "marktprijs_inkoop",          False, "total_consumption_kwh", False,
         f"gem. € {avg_per_kwh(bd, 'marktprijs_inkoop', 'total_consumption_kwh'):.4f}/kWh"),
        ("Energiebelasting",                 "energiebelasting",           False, "energiebelasting_kwh",  False,
         f"€ {bd['tarief_energiebelasting_per_kwh']:.4f}/kWh"),
        ("Leveranciersopslag inkoop",        "leveranciersopslag_inkoop",  False, "total_consumption_kwh", False,
         f"€ {bd['tarief_leveranciersopslag_inkoop_per_kwh']:.4f}/kWh"),
        ("Leveranciersopslag teruglevering", "leveranciersopslag_verkoop", False, "total_feed_in_kwh",     False,
         f"€ {bd['tarief_leveranciersopslag_verkoop_per_kwh']:.4f}/kWh"),
        ("Teruglevering opbrengst",          "teruglevering_opbrengst",    True,  "total_feed_in_kwh",     False,
         f"gem. € {avg_per_kwh(bd, 'teruglevering_opbrengst', 'total_feed_in_kwh'):.4f}/kWh"),
    ]
    rows = [r for r in all_rows_def if include_fixed or not r[4]]

    def get_contribution(breakdown, key, is_credit):
        raw = breakdown[key]
        return -raw if is_credit else raw

    def fmt_amount(amount):
        return f"−€ {abs(amount):,.2f}" if amount < 0 else f"€ {amount:,.2f}"

    col_widths = [3, 2] + [2] * len(all_results)
    header_cols = st.columns(col_widths)
    header_cols[0].markdown("**Post**")
    header_cols[1].markdown("**Zonder batterij**")
    for i, res in enumerate(all_results):
        header_cols[i + 2].markdown(f"**{res['label']}**")
    st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)

    prev_fixed = None
    for row_label, key, is_credit, vol_key, is_fixed, tarief_str in rows:
        if include_fixed:
            if prev_fixed is None:
                st.markdown("<div style='margin:0 0 2px 0; font-size:0.75rem; color:grey; text-transform:uppercase; letter-spacing:0.05em'>Vaste kosten</div>", unsafe_allow_html=True)
            elif prev_fixed != is_fixed:
                st.markdown("<div style='margin:6px 0 2px 0; font-size:0.75rem; color:grey; text-transform:uppercase; letter-spacing:0.05em'>Variabele kosten</div>", unsafe_allow_html=True)
        prev_fixed = is_fixed

        base_amount = get_contribution(breakdown_baseline, key, is_credit)
        row_cols = st.columns(col_widths)
        row_cols[0].markdown(f"{row_label} <small style='color:grey'>({tarief_str})</small>", unsafe_allow_html=True)
        row_cols[1].markdown(fmt_amount(base_amount))

        for i, res in enumerate(all_results):
            sim_amount = get_contribution(res['breakdown_simulated'], key, is_credit)
            diff = base_amount - sim_amount
            cell = fmt_amount(sim_amount)
            if abs(diff) >= 0.005:
                arrow, color = ("▼", "green") if diff > 0 else ("▲", "red")
                cell += f" <small style='color:{color}'>({arrow} € {abs(diff):,.2f})</small>"
            row_cols[i + 2].markdown(cell, unsafe_allow_html=True)

    st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)
    total_cols = st.columns(col_widths)
    total_cols[0].markdown("**Totaal**")
    total_cols[1].markdown(f"**€ {display_baseline:,.2f}**")
    for i, (res, dc) in enumerate(zip(all_results, display_costs)):
        diff = display_baseline - dc
        if diff > 0:
            diff_html = f"<span style='color:green'>▼ € {diff:,.2f}</span>"
        else:
            diff_html = f"<span style='color:red'>▲ € {abs(diff):,.2f}</span>"
        total_cols[i + 2].markdown(f"**€ {dc:,.2f}** {diff_html}", unsafe_allow_html=True)


# ── Per-battery results renderer ──────────────────────────────────────────────

def _render_battery_detail(res, display_baseline, breakdown_baseline, include_fixed, strategy, key_prefix="bat"):
    result = res['result']
    battery = res['battery']
    breakdown_simulated = res['breakdown_simulated']
    cost_simulated = res['cost_simulated']

    if not include_fixed and breakdown_simulated:
        fixed_sim = (breakdown_simulated['abonnementskosten']
                     + breakdown_simulated['netbeheerskosten']
                     - breakdown_simulated['belastingvermindering'])
        display_cost = cost_simulated - fixed_sim
    else:
        display_cost = cost_simulated
    display_savings = display_baseline - display_cost

    if strategy == "MPC":
        col1, col2, col3, col4 = st.columns(4)
        realistic_savings = display_savings * 0.8
        col1.metric("Jaarnota (Met Batterij)", f"€{display_cost:.2f}")
        col2.metric("Geschatte Besparing", f"€{display_savings:.2f}", delta=f"{display_savings:.2f}")
        col3.metric("Realistische Besparing (80%)", f"€{realistic_savings:.2f}",
                    help="In de werkelijkheid kan een algoritme nooit een perfecte voorspelling doen van het energieverbruik en de zonne-opbrengst. Deze waarde geeft een realistischer beeld van de te verwachten besparing.")
        col4.metric("Batterij Cycli 🔄", f"{getattr(result, 'total_cycles', 0.0):.1f}")
    else:
        col1, col2, col3 = st.columns(3)
        col1.metric("Jaarnota (Met Batterij)", f"€{display_cost:.2f}")
        col2.metric("Geschatte Besparing", f"€{display_savings:.2f}", delta=f"{display_savings:.2f}")
        col3.metric("Batterij Cycli 🔄", f"{getattr(result, 'total_cycles', 0.0):.1f}")

    if breakdown_baseline and breakdown_simulated:
        with st.expander("Kostenopbouw", expanded=True):
            bd = breakdown_baseline

            def avg_per_kwh(breakdown, cost_key, vol_key):
                vol = breakdown[vol_key]
                return breakdown[cost_key] / vol if vol else 0.0

            all_rows = [
                ("Abonnementskosten",               "abonnementskosten",         False, None,                    True,
                 f"€ {bd['tarief_abonnementskosten']:.2f}/jaar"),
                ("Netbeheerskosten",                 "netbeheerskosten",           False, None,                    True,
                 f"€ {bd['tarief_netbeheerskosten']:.2f}/jaar"),
                ("Belastingvermindering",            "belastingvermindering",      True,  None,                    True,
                 f"€ {bd['tarief_belastingvermindering']:.2f}/jaar"),
                ("Marktprijs inkoop",                "marktprijs_inkoop",          False, "total_consumption_kwh", False,
                 f"gem. € {avg_per_kwh(bd, 'marktprijs_inkoop', 'total_consumption_kwh'):.4f}/kWh"),
                ("Energiebelasting",                 "energiebelasting",           False, "energiebelasting_kwh",  False,
                 f"€ {bd['tarief_energiebelasting_per_kwh']:.4f}/kWh"),
                ("Leveranciersopslag inkoop",        "leveranciersopslag_inkoop",  False, "total_consumption_kwh", False,
                 f"€ {bd['tarief_leveranciersopslag_inkoop_per_kwh']:.4f}/kWh"),
                ("Leveranciersopslag teruglevering", "leveranciersopslag_verkoop", False, "total_feed_in_kwh",     False,
                 f"€ {bd['tarief_leveranciersopslag_verkoop_per_kwh']:.4f}/kWh"),
                ("Teruglevering opbrengst",          "teruglevering_opbrengst",    True,  "total_feed_in_kwh",     False,
                 f"gem. € {avg_per_kwh(bd, 'teruglevering_opbrengst', 'total_feed_in_kwh'):.4f}/kWh"),
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
            for row_label, key, is_credit, vol_key, is_fixed, tarief_str in rows:
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
                col_label.markdown(f"{row_label} <small style='color:grey'>({tarief_str})</small>", unsafe_allow_html=True)
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
            total_diff = display_baseline - display_cost
            col_label.markdown("**Totaal**")
            col_base.markdown(f"**€ {display_baseline:,.2f}**")
            col_sim.markdown(f"**€ {display_cost:,.2f}**")
            if total_diff > 0:
                col_diff.markdown(f"<span style='color:green'>**▼ € {total_diff:,.2f}**</span>", unsafe_allow_html=True)
            else:
                col_diff.markdown(f"<span style='color:red'>**▲ € {abs(total_diff):,.2f}**</span>", unsafe_allow_html=True)

    with st.expander("Interactieve Energieflow", expanded=True):
        duration = result.df['timestamp'].max() - result.df['timestamp'].min()
        if duration > pd.Timedelta(days=32):
            st.info("De simulatie beslaat een lange periode. Hieronder zie je representatieve weken voor verschillende seizoenen en het totaaloverzicht.")
            seasons = {
                "❄️ Winter (Jan)": 1,
                "🌱 Lente (Apr)": 4,
                "☀️ Zomer (Jul)": 7,
                "🍂 Herfst (Okt)": 10,
            }
            available_seasons = {}
            for name, month in seasons.items():
                mask = result.df['timestamp'].dt.month == month
                if mask.any():
                    start_time = result.df[mask]['timestamp'].min()
                    end_time = start_time + pd.Timedelta(days=7)
                    available_seasons[name] = result.df[
                        (result.df['timestamp'] >= start_time) & (result.df['timestamp'] < end_time)
                    ]
            tab_names = ["📊 Volledige Periode"] + list(available_seasons.keys())
            if not available_seasons:
                tab_names += ["Begin van periode", "Einde van periode"]
            chart_tabs = st.tabs(tab_names)
            for i, t_name in enumerate(tab_names):
                with chart_tabs[i]:
                    if i == 0:
                        plot_df = result.df
                        slice_title = "Volledige Periode"
                    else:
                        s_name = (list(available_seasons.keys())[i - 1]
                                  if available_seasons
                                  else ["Begin van periode", "Einde van periode"][i - 1])
                        plot_df = (available_seasons[s_name]
                                   if available_seasons
                                   else (result.df.head(7 * 24 * 4) if i == 1 else result.df.tail(7 * 24 * 4)))
                        slice_title = s_name
                    st.plotly_chart(create_usage_chart(plot_df, title=f"Huisverbruik & Zon vs Batterij Status (%) - {slice_title}"), use_container_width=True, key=f"{key_prefix}_usage_{i}")
                    st.plotly_chart(create_price_chart(plot_df, title=f"Marktprijs vs Batterij SoC (kWh) - {slice_title}"), use_container_width=True, key=f"{key_prefix}_price_{i}")
        else:
            st.plotly_chart(create_usage_chart(result.df, title="Huisverbruik & Zon vs Batterij Status (%)"), use_container_width=True, key=f"{key_prefix}_usage")
            st.plotly_chart(create_price_chart(result.df, title="Marktprijs vs Batterij SoC (kWh)"), use_container_width=True, key=f"{key_prefix}_price")

    with st.expander("Bekijk Ruwe Simulatiedata"):
        st.dataframe(result.df.head(100))

# ── Secrets / API Key ─────────────────────────────────────────────────────────

try:
    ENTSOE_API_KEY = st.secrets["ENTSOE_API_KEY"]
except Exception:
    ENTSOE_API_KEY = None

try:
    SMP_API_KEY_DEFAULT = st.secrets["SLIMMEMETERPORTAL_API_KEY"]
except Exception:
    SMP_API_KEY_DEFAULT = None

# ── Page header ───────────────────────────────────────────────────────────────

st.title("🔋 BattWatt: Thuisbatterij Evaluator")
st.markdown("""
Evalueer de impact van een thuisbatterij op je energierekening met de Nederlandse marktdynamiek.
Upload je P1-metergegevens om te beginnen.
""")
st.markdown(
    """
    <style>
    .bw-privacy-note {
        display: flex; align-items: center; gap: 4px;
        font-size: 0.875rem; color: rgba(128, 128, 128, 0.9);
        margin-top: -0.5rem; margin-bottom: 1rem;
    }
    .bw-privacy-note details { position: relative; display: inline-block; }
    .bw-privacy-note summary { list-style: none; cursor: pointer; }
    .bw-privacy-note summary::-webkit-details-marker { display: none; }
    .bw-privacy-note summary .bw-icon {
        display: inline-flex; align-items: center; justify-content: center;
        width: 15px; height: 15px; border-radius: 50%;
        border: 1px solid currentColor; font-size: 10px; line-height: 1;
        opacity: 0.7;
    }
    .bw-privacy-note .bw-tooltip {
        position: absolute; z-index: 100; top: 22px; left: 0;
        background: var(--bw-tooltip-bg, #ffffff); color: inherit;
        border: 1px solid rgba(128, 128, 128, 0.3); border-radius: 6px;
        padding: 8px 10px; width: 260px; font-size: 0.8rem; line-height: 1.4;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
    }
    .bw-privacy-note .bw-tooltip a { color: inherit; text-decoration: underline; }
    @media (prefers-color-scheme: dark) {
        .bw-privacy-note .bw-tooltip { --bw-tooltip-bg: #262730; }
    }
    </style>
    <div class="bw-privacy-note">
        🔒 Deze applicatie slaat <u>geen enkele</u> data op.
        <details>
            <summary><span class="bw-icon">?</span></summary>
            <div class="bw-tooltip">
                Alle uploads en berekeningen blijven binnen uw sessie. Deze app wordt gehost op
                Streamlit Community Cloud — zie het
                <a href="https://streamlit.io/privacy-policy" target="_blank" rel="noopener">Streamlit privacybeleid</a>
                voor meer informatie.
            </div>
        </details>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Sidebar ───────────────────────────────────────────────────────────────────

st.sidebar.markdown("## Simulatie Configuratie")
st.sidebar.divider()

# 1. Meter Data
st.sidebar.subheader("📂 Meter Data")
meter_source = st.sidebar.radio("Databron", ["Bestand upload", "SlimmeMeterPortal API"], horizontal=True)

uploaded_meter = None
custom_mapping = None

if meter_source == "Bestand upload":
    uploaded_meter = st.sidebar.file_uploader("Upload Meter Data (CSV of Excel)", type=["csv", "xlsx"])

    with st.sidebar.expander("ℹ️ Ondersteunde Formaten"):
        st.markdown("""
        **Automatisch Herkend:**
        - HomeWizard CSV (Export uit app)
        - SlimmeMeterPortal.nl dag xls
        - Kwartierdata single-column Excel (Datum Tijd + netto vermogen, positief = verbruik, negatief = productie)
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
        single_signed_col = st.checkbox(
            "Eén kolom met signed waarde (positief = verbruik, negatief = productie)",
            value=False
        )
        if single_signed_col:
            col_value = st.text_input("Kolomnaam Netto Vermogen", value="waarde")
        else:
            col_imp = st.text_input("Kolomnaam Verbruik/Import", value="verbruik")
            col_exp = st.text_input("Kolomnaam Teruglevering/Export", value="teruglevering")
        is_cum = st.checkbox("Meterstanden zijn cumulatief", value=False)

        if use_custom_mapping:
            if single_signed_col:
                columns = {
                    "timestamp": col_time,
                    "value": col_value
                }
            else:
                columns = {
                    "timestamp": col_time,
                    "import": col_imp,
                    "export": col_exp
                }
            custom_mapping = {
                "format": fmt,
                "delimiter": sep,
                "decimal": dec,
                "columns": columns,
                "is_cumulative": is_cum
            }

else:  # SlimmeMeterPortal API
    smp_api_key = st.sidebar.text_input(
        "API-Key",
        type="password",
        value=st.session_state.get("smp_api_key", SMP_API_KEY_DEFAULT or ""),
        help="API-Key uit je SlimmeMeterPortal.nl account.",
    )

    if st.sidebar.button("🔌 Aansluitingen ophalen"):
        st.session_state["smp_api_key"] = smp_api_key
        st.session_state.pop("smp_meter_df", None)
        st.session_state.pop("smp_missing_dates", None)
        try:
            client = SlimmeMeterPortalClient(api_key=smp_api_key)
            st.session_state["smp_connections"] = client.get_connections()
        except SlimmeMeterPortalError as e:
            st.session_state.pop("smp_connections", None)
            st.sidebar.error(f"Fout bij ophalen aansluitingen: {e}")

    connections = st.session_state.get("smp_connections")
    if connections:
        conn_options = {f"{c.meter_identifier} ({c.connection_type})": c for c in connections}
        selected_conn_label = st.sidebar.selectbox("Aansluiting", list(conn_options.keys()))
        selected_connection = conn_options[selected_conn_label]

        date_col1, date_col2 = st.sidebar.columns(2)
        smp_start_date = date_col1.date_input(
            "Vanaf", value=pd.Timestamp.now().normalize() - pd.Timedelta(days=30), format="YYYY-MM-DD"
        )
        smp_end_date = date_col2.date_input("Tot en met", value=pd.Timestamp.now().normalize(), format="YYYY-MM-DD")

        if st.sidebar.button("⬇️ Data ophalen"):
            if smp_start_date > smp_end_date:
                st.sidebar.error("'Vanaf' moet voor 'Tot en met' liggen.")
            else:
                dates = list(pd.date_range(smp_start_date, smp_end_date, freq="D").date)
                progress_bar = st.sidebar.progress(0, text="Data ophalen via SlimmeMeterPortal API...")
                try:
                    client = SlimmeMeterPortalClient(api_key=st.session_state["smp_api_key"])
                    day_responses = client.get_usage_range(
                        selected_connection.meter_identifier,
                        dates,
                        progress_callback=lambda cur, tot: progress_bar.progress(
                            cur / tot, text=f"Data ophalen: dag {cur}/{tot}"
                        ),
                    )
                    usages = flatten_usage_range(day_responses)
                    st.session_state["smp_meter_df"] = SlimmeMeterPortalAPILoader().load_usages(usages)
                    st.session_state["smp_missing_dates"] = find_missing_dates(dates, day_responses)
                except (SlimmeMeterPortalError, ValueError) as e:
                    st.session_state.pop("smp_meter_df", None)
                    st.session_state.pop("smp_missing_dates", None)
                    st.sidebar.error(f"Fout bij ophalen data: {e}")
                finally:
                    progress_bar.empty()

    if st.session_state.get("smp_meter_df") is not None:
        st.sidebar.success(f"{len(st.session_state['smp_meter_df'])} intervallen geladen.")
        missing_dates = st.session_state.get("smp_missing_dates") or []
        if missing_dates:
            shown = ", ".join(d.strftime("%Y-%m-%d") for d in missing_dates[:5])
            extra = f" en {len(missing_dates) - 5} andere dag(en)" if len(missing_dates) > 5 else ""
            st.sidebar.warning(f"⚠️ Geen data voor {len(missing_dates)} dag(en): {shown}{extra}.")

st.sidebar.divider()

# 2. Batteries — dynamic list
st.sidebar.subheader("🔋 Batterijen")

if "battery_ids" not in st.session_state:
    st.session_state.battery_ids = [0]
    st.session_state.battery_counter = 1
    st.session_state["bat_label_0"] = "10 kWh"
    st.session_state["bat_preset_0"] = "10 kWh"

for bid in list(st.session_state.battery_ids):
    # Ensure defaults exist for any battery added via the button
    if f"bat_label_{bid}" not in st.session_state:
        st.session_state[f"bat_label_{bid}"] = "10 kWh"
    if f"bat_preset_{bid}" not in st.session_state:
        st.session_state[f"bat_preset_{bid}"] = "10 kWh"

    idx_in_list = st.session_state.battery_ids.index(bid) + 1
    expander_label = f"Batterij {idx_in_list}: {st.session_state[f'bat_label_{bid}']}"
    with st.sidebar.expander(expander_label, expanded=(idx_in_list == 1)):
        col_l, col_d = st.columns([5, 1])
        with col_l:
            st.text_input("Label", key=f"bat_label_{bid}")
        with col_d:
            st.write("")
            if len(st.session_state.battery_ids) > 1:
                if st.button("🗑", key=f"del_{bid}"):
                    st.session_state.battery_ids.remove(bid)
                    st.rerun()

        preset_default_idx = (_battery_preset_options.index(st.session_state[f"bat_preset_{bid}"])
                              if st.session_state[f"bat_preset_{bid}"] in _battery_preset_options else 1)
        st.selectbox("Sjabloon", _battery_preset_options, index=preset_default_idx, key=f"bat_preset_{bid}")

        if st.session_state.get(f"bat_preset_{bid}") == "Handmatig invoeren (Custom)":
            st.number_input("Capaciteit (kWh)", value=10.0, step=0.5, key=f"bat_cap_{bid}")
            st.number_input("Max. Laadvermogen (kW)", value=3.68, step=0.1, key=f"bat_charge_{bid}")
            st.number_input("Max. Ontlaadvermogen (kW)", value=3.68, step=0.1, key=f"bat_discharge_{bid}")
            st.slider("Laadefficiëntie (%)", 80, 100, 98, key=f"bat_eff_c_{bid}")
            st.slider("Ontlaadefficiëntie (%)", 80, 100, 98, key=f"bat_eff_d_{bid}")

if st.sidebar.button("➕ Batterij toevoegen", use_container_width=True):
    new_id = st.session_state.battery_counter
    st.session_state.battery_counter += 1
    st.session_state.battery_ids.append(new_id)
    st.session_state[f"bat_label_{new_id}"] = "10 kWh"
    st.session_state[f"bat_preset_{new_id}"] = "10 kWh"
    st.rerun()

st.sidebar.divider()

# 3. Provider
st.sidebar.subheader("💶 Energieleverancier")
with st.sidebar.expander("Provider Details", expanded=True):
    custom_name = st.text_input("Naam", value="Mijn Leverancier")
    custom_sub = st.number_input(
        "Vaste leveringskosten (€/jaar)", value=75.0, step=1.0,
        help="Jaarlijks vast bedrag dat je leverancier rekent bovenop de variabele energiekosten."
    )
    custom_buy = st.number_input(
        "Leveranciersopslag inkoop (€/kWh incl. BTW)", value=0.02, format="%.4f",
        help="Opslag die je leverancier rekent per kWh die je van het net afneemt, bovenop de marktprijs en energiebelasting."
    )
    custom_sell = st.number_input(
        "Leveranciersopslag teruglevering (€/kWh incl. BTW)", value=0.02, format="%.4f",
        help="Kosten die je leverancier rekent per kWh die je teruglevert aan het net (een negatieve vergoeding)."
    )

provider = Provider(
    name=custom_name,
    subscription_cost=custom_sub,
    buying_fee=custom_buy,
    selling_fee=custom_sell,
    net_metering=False,
    selling_fee_net_metering=True
)

st.sidebar.divider()

# 4. Strategy
st.sidebar.subheader("🎛️ Aansturing")
strategy_map = {
    "PV Prioriteit (Zelfconsumptie)": "PV",
    "Kosten Optimaal (MPC)": "MPC"
}
selected_strategy = st.sidebar.selectbox(
    "Selecteer Strategie", list(strategy_map.keys()),
    help="**PV Prioriteit:** laadt de batterij direct op met zonnestroom en ontlaadt bij verbruik. Eenvoudig en snel.\n\n**Kosten Optimaal (MPC):** optimaliseert laden/ontladen op basis van de verwachte marktprijzen over de komende 24 uur. Geeft doorgaans een hogere besparing, maar vereist een goede prijsvoorspelling."
)

# Price data always via API; manual upload kept in back-end only
price_source = "Automatisch (ENTSO-E API)"
uploaded_price = None

st.sidebar.divider()

# ── Simulate button ───────────────────────────────────────────────────────────

can_simulate = False
meter_data_ready = bool(uploaded_meter) if meter_source == "Bestand upload" else st.session_state.get("smp_meter_df") is not None
if meter_data_ready:
    can_simulate = True
    if not ENTSOE_API_KEY:
        st.sidebar.error("⚠️ Geen API Key geconfigureerd.")
        can_simulate = False

def _run_simulation(meter_df):
    """Fetch prices, run baseline + all battery configs sequentially, store results."""
    with st.status("Data verwerken en simulatie uitvoeren...", expanded=True) as status:
        # Prices
        start_date = meter_df['timestamp'].min()
        end_date = meter_df['timestamp'].max()
        st.write(f"Marktprijzen ophalen via API ({start_date.date()} tot {end_date.date()})...")
        try:
            price_df = fetch_entsoe_prices(ENTSOE_API_KEY, start_date, end_date)
        except Exception as e:
            st.error(f"Fout bij ophalen prijzen: {e}")
            st.stop()

        # Merge
        st.write("Data samenvoegen...")
        merged_df = merge_data(meter_df, price_df)
        merged_df['day_ahead_price'] = merged_df['day_ahead_price'] / 1000
        merged_df.set_index("timestamp", drop=False, inplace=True)

        # Baseline (no battery)
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
        billing = BillingEngine(provider)
        cost_baseline = billing.calculate_bill(baseline_result)
        breakdown_baseline = billing.calculate_bill_breakdown(baseline_result)

        # Sequential simulation per battery
        battery_ids = list(st.session_state.battery_ids)
        all_results = []

        for i, bid in enumerate(battery_ids):
            label = st.session_state.get(f"bat_label_{bid}", f"Batterij {i + 1}")
            battery = _battery_from_id(bid)
            st.write(f"Simulatie {i + 1}/{len(battery_ids)}: **{label}**...")

            if strategy_map[selected_strategy] == "PV":
                controller = Controller_PV(battery)
            else:
                controller = Controller_MPC(battery, merged_df, provider,
                                            horizon_hours=24.0, reoptimize_every_hours=12.0)

            simulator = Simulator(battery, controller)
            progress_bar = st.progress(0, text=f"{label}: simulatie voortgang")

            def _make_callback(pb, lbl):
                def _cb(current, total):
                    pb.progress(current / total, text=f"{lbl}: {current}/{total} stappen")
                return _cb

            result = simulator.run(merged_df, progress_callback=_make_callback(progress_bar, label))
            progress_bar.empty()
            result.df['battery_soc_kwh'] = result.df['battery_soc'] * battery.capacity_kwh / 100

            cost_simulated = billing.calculate_bill(result)
            breakdown_simulated = billing.calculate_bill_breakdown(result)

            all_results.append({
                'label': label,
                'battery': battery,
                'result': result,
                'cost_simulated': cost_simulated,
                'savings': cost_baseline - cost_simulated,
                'breakdown_simulated': breakdown_simulated,
            })

        status.update(label="Simulatie Voltooid!", state="complete", expanded=False)
        st.session_state['simulation_result'] = {
            'cost_baseline': cost_baseline,
            'breakdown_baseline': breakdown_baseline,
            'all_results': all_results,
            'strategy': strategy_map[selected_strategy],
        }


if st.sidebar.button("🚀 Start Simulatie", use_container_width=True, type="primary", disabled=not can_simulate):
    try:
        if meter_source == "Bestand upload":
            meter_df, data_checks = SmartLoader.load_with_checks(uploaded_meter, config=custom_mapping)
        else:
            meter_df, data_checks = st.session_state["smp_meter_df"], []
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

# ── Main area ─────────────────────────────────────────────────────────────────

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
    cost_baseline = res_data['cost_baseline']
    breakdown_baseline = res_data['breakdown_baseline']
    all_results = res_data['all_results']
    strategy = res_data['strategy']

    st.header("Resultaten Overzicht")
    include_fixed = st.checkbox(
        "Vaste kosten meenemen", value=True,
        help="Vaste kosten omvatten abonnementskosten, netbeheerskosten en belastingvermindering. Zet uit om alleen het variabele deel te vergelijken."
    )

    def _display_cost(res):
        bd = res['breakdown_simulated']
        if not include_fixed and bd:
            fixed = bd['abonnementskosten'] + bd['netbeheerskosten'] - bd['belastingvermindering']
            return res['cost_simulated'] - fixed
        return res['cost_simulated']

    if not include_fixed and breakdown_baseline:
        fixed_base = (breakdown_baseline['abonnementskosten']
                      + breakdown_baseline['netbeheerskosten']
                      - breakdown_baseline['belastingvermindering'])
        display_baseline = cost_baseline - fixed_base
    else:
        display_baseline = cost_baseline

    display_costs = [_display_cost(r) for r in all_results]

    # ── Summary metrics ──────────────────────────────────────────────────────
    cols = st.columns(1 + len(all_results))
    cols[0].metric("Baseline (Geen Batterij)", f"€{display_baseline:.2f}")
    for i, (res, dc) in enumerate(zip(all_results, display_costs)):
        savings = display_baseline - dc
        cols[i + 1].metric(res['label'], f"€{dc:.2f}", delta=f"€{savings:.2f} besparing")

    # Cycles + MPC realistic savings row
    extra_cols = st.columns(1 + len(all_results))
    extra_cols[0].markdown("")
    for i, res in enumerate(all_results):
        cycles = getattr(res['result'], 'total_cycles', 0.0)
        if strategy == "MPC":
            realistic = (display_baseline - display_costs[i]) * 0.8
            extra_cols[i + 1].metric(
                "Batterij Cycli 🔄", f"{cycles:.1f}",
                help=f"Realistische besparing (80% van geschatte besparing): €{realistic:.2f}. In de werkelijkheid kan een algoritme nooit een perfecte voorspelling doen van het energieverbruik en de zonne-opbrengst."
            )
        else:
            extra_cols[i + 1].metric("Batterij Cycli 🔄", f"{cycles:.1f}")

    st.caption("⚠️ **Let op:** Deze waarden zijn schattingen gebaseerd op historische data en simulatiemodellen. De werkelijke resultaten kunnen afwijken door o.a. weersomstandigheden, batterij-degradatie en wijzigingen in markttarieven. Gebruik deze resultaten enkel ter oriëntatie.")

    st.divider()

    # ── Unified cost breakdown ───────────────────────────────────────────────
    if breakdown_baseline and all(r['breakdown_simulated'] for r in all_results):
        with st.expander("Kostenopbouw Vergelijking", expanded=True):
            _render_unified_breakdown(all_results, display_baseline, display_costs, breakdown_baseline, include_fixed)

    st.divider()

    # ── Chart viewer with toggle ─────────────────────────────────────────────
    st.subheader("Interactieve Energieflow")

    # Build unique pill labels (handle duplicate battery names)
    pill_labels = []
    seen_labels: dict = {}
    for res in all_results:
        lbl = res['label']
        if lbl in seen_labels:
            seen_labels[lbl] += 1
            pill_labels.append(f"{lbl} ({seen_labels[lbl]})")
        else:
            seen_labels[lbl] = 1
            pill_labels.append(lbl)

    if len(all_results) > 1:
        selected_pills = st.pills(
            "Selecteer batterijen voor weergave",
            pill_labels,
            selection_mode="multi",
            default=pill_labels[:1],
            key="chart_battery_pills",
        )
    else:
        selected_pills = pill_labels

    selected_indices = [i for i, lbl in enumerate(pill_labels) if lbl in (selected_pills or [])]

    if not selected_indices:
        st.info("Selecteer een of meer batterijen hierboven om de grafieken te bekijken.")

    for pos, idx in enumerate(selected_indices):
        res = all_results[idx]
        result = res['result']
        battery = res['battery']
        key_prefix = f"bat_{idx}"

        if len(selected_indices) > 1:
            st.markdown(f"#### {pill_labels[idx]}")

        duration = result.df['timestamp'].max() - result.df['timestamp'].min()
        if duration > pd.Timedelta(days=32):
            st.info("De simulatie beslaat een lange periode. Hieronder zie je representatieve weken voor verschillende seizoenen en het totaaloverzicht.")
            seasons = {
                "❄️ Winter (Jan)": 1,
                "🌱 Lente (Apr)": 4,
                "☀️ Zomer (Jul)": 7,
                "🍂 Herfst (Okt)": 10,
            }
            available_seasons = {}
            for name, month in seasons.items():
                mask = result.df['timestamp'].dt.month == month
                if mask.any():
                    start_time = result.df[mask]['timestamp'].min()
                    end_time = start_time + pd.Timedelta(days=7)
                    available_seasons[name] = result.df[
                        (result.df['timestamp'] >= start_time) & (result.df['timestamp'] < end_time)
                    ]
            tab_names = ["📊 Volledige Periode"] + list(available_seasons.keys())
            if not available_seasons:
                tab_names += ["Begin van periode", "Einde van periode"]
            chart_tabs = st.tabs(tab_names)
            for i, t_name in enumerate(tab_names):
                with chart_tabs[i]:
                    if i == 0:
                        plot_df = result.df
                        slice_title = "Volledige Periode"
                    else:
                        s_name = (list(available_seasons.keys())[i - 1]
                                  if available_seasons
                                  else ["Begin van periode", "Einde van periode"][i - 1])
                        plot_df = (available_seasons[s_name]
                                   if available_seasons
                                   else (result.df.head(7 * 24 * 4) if i == 1 else result.df.tail(7 * 24 * 4)))
                        slice_title = s_name
                    st.plotly_chart(create_usage_chart(plot_df, title=f"Huisverbruik & Zon vs Batterij Status (%) - {slice_title}"), use_container_width=True, key=f"{key_prefix}_usage_{i}")
                    st.plotly_chart(create_price_chart(plot_df, title=f"Marktprijs vs Batterij SoC (kWh) - {slice_title}"), use_container_width=True, key=f"{key_prefix}_price_{i}")
        else:
            st.plotly_chart(create_usage_chart(result.df, title="Huisverbruik & Zon vs Batterij Status (%)"), use_container_width=True, key=f"{key_prefix}_usage")
            st.plotly_chart(create_price_chart(result.df, title="Marktprijs vs Batterij SoC (kWh)"), use_container_width=True, key=f"{key_prefix}_price")

        if pos < len(selected_indices) - 1:
            st.divider()

    # ── Raw data ─────────────────────────────────────────────────────────────
    with st.expander("Bekijk Ruwe Simulatiedata"):
        if len(all_results) > 1:
            raw_tabs = st.tabs(pill_labels)
            for tab, res in zip(raw_tabs, all_results):
                with tab:
                    st.dataframe(res['result'].df.head(100))
        else:
            st.dataframe(all_results[0]['result'].df.head(100))

elif not uploaded_meter:
    st.info("👈 Upload je P1-metergegevens in de zijbalk om de berekening te starten.")
