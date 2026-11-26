import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from pathlib import Path
from data_loader import SmartLoader, load_price_data, fetch_entsoe_prices, merge_data, SlimmeMeterPortalAPILoader, EntsoeFetchError
from slimmemeterportal_client import SlimmeMeterPortalClient, SlimmeMeterPortalError, flatten_usage_range, find_missing_dates
from energy_providers import Provider, get_energy_tax_incl_vat, tariff_incl_energy_tax
from battery import get_battery, Battery
from controllers import Controller_PV, Controller_MPC
from simulator import Simulator
from billing import BillingEngine, compare_providers
from models import SimulationResult
from results_tables import (
    SWEEP_SIZES_KWH, SWEEP_SIZE_PRESETS, SWEEP_EFFICIENCIES,
    breakdown_row_defs as _breakdown_row_defs,
    breakdown_dataframe as _breakdown_dataframe,
    sweep_summary_dataframe as _sweep_summary_dataframe,
    unique_labels as _unique_labels,
    contract_label as _contract_label,
    provider_cost as _provider_cost,
    provider_comparison_dataframe as _provider_comparison_dataframe,
    sweep_provider_dataframe as _sweep_provider_dataframe,
    cheapest_option as _cheapest_option,
    all_option_costs as _all_option_costs,
)
# Failure reporting is optional: if the module is broken or a dependency is
# missing, the app must still work, just without alerts.
try:
    import error_reporting
except Exception:  # noqa: BLE001
    error_reporting = None
# The PDF report is optional too: without it the app just has no report button.
try:
    import report
except Exception:  # noqa: BLE001
    report = None
import base64
import time

# Page configuration
st.set_page_config(page_title="JortApp - Thuisbatterij Evaluator", layout="wide", page_icon="🔋")

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

PLOT_CONFIG = {
    "displaylogo": False,
    "displayModeBar": True,
    "toImageButtonOptions": {"format": "png", "scale": 3, "filename": "JortApp_grafiek"},
}


def _style_figure(fig, height=500):
    """Large, copy-friendly typography and a white background so a screenshot or
    PNG export stays legible when pasted into a document."""
    fig.update_layout(
        height=height,
        template="plotly_white",
        font=dict(size=16),
        title_font=dict(size=18),
        legend=dict(font=dict(size=15)),
        margin=dict(l=70, r=70, t=90, b=70),
    )
    fig.update_xaxes(tickfont=dict(size=15), title_font=dict(size=16))
    fig.update_yaxes(tickfont=dict(size=15), title_font=dict(size=16))
    return fig


def _csv_bytes(df, index=False):
    """Semicolon-separated with decimal comma and BOM: opens directly in Dutch Excel."""
    return df.to_csv(index=index, sep=";", decimal=",").encode("utf-8-sig")


def _download_csv(df, label, filename, key, index=False):
    st.download_button(label, _csv_bytes(df, index=index), file_name=filename,
                       mime="text/csv", key=key)


def _download_report(res_data, include_fixed, key):
    """'Download volledig rapport' button. The PDF is only built when clicked
    (deferred download) and cached on the stored result, per include_fixed, so
    reruns stay fast and a second click doesn't rebuild it."""
    if report is None:
        return

    def _build():
        cache = res_data.setdefault('_report_pdf', {})
        if include_fixed not in cache:
            cache[include_fixed] = report.build_report(res_data, include_fixed=include_fixed)
        return cache[include_fixed]

    st.download_button("📄 Download volledig rapport (PDF)", _build, file_name=report.REPORT_FILENAME,
                       mime="application/pdf", key=key, on_click="ignore",
                       help="Instellingen, resultaten, kostenopbouw en grafieken van deze simulatie in één PDF. "
                            "Het rapport wordt alleen in je sessie gemaakt en nergens opgeslagen.")


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
        hovermode="x unified"
    )
    return _style_figure(fig, height=500)

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
        hovermode="x unified"
    )
    return _style_figure(fig, height=500)

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
            efficiency=st.session_state.get(f"bat_eff_{bid}", 90) / 100,
            standby_power_w=st.session_state.get(f"bat_standby_{bid}", 10.0),
        )
    return get_battery(_battery_display_to_key[preset])

# ── Unified breakdown comparison table ────────────────────────────────────────

def _render_unified_breakdown(all_results, display_baseline, display_costs, breakdown_baseline, include_fixed):
    bd = breakdown_baseline

    all_rows_def = _breakdown_row_defs(bd)
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

    table_df = _breakdown_dataframe(all_results, breakdown_baseline, display_baseline, display_costs, include_fixed)
    with st.expander("📋 Kostenopbouw als tabel (kopiëren / exporteren)"):
        st.dataframe(table_df, use_container_width=True, hide_index=True)
        _download_csv(table_df, "⬇️ Download kostenopbouw (CSV)", "JortApp_kostenopbouw.csv",
                      key="breakdown_csv")


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

            all_rows = _breakdown_row_defs(bd)
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
                    st.plotly_chart(create_usage_chart(plot_df, title=f"Huisverbruik & Zon vs Batterij Status (%) - {slice_title}"), use_container_width=True, config=PLOT_CONFIG, key=f"{key_prefix}_usage_{i}")
                    st.plotly_chart(create_price_chart(plot_df, title=f"Marktprijs vs Batterij SoC (kWh) - {slice_title}"), use_container_width=True, config=PLOT_CONFIG, key=f"{key_prefix}_price_{i}")
        else:
            st.plotly_chart(create_usage_chart(result.df, title="Huisverbruik & Zon vs Batterij Status (%)"), use_container_width=True, config=PLOT_CONFIG, key=f"{key_prefix}_usage")
            st.plotly_chart(create_price_chart(result.df, title="Marktprijs vs Batterij SoC (kWh)"), use_container_width=True, config=PLOT_CONFIG, key=f"{key_prefix}_price")

    with st.expander("Bekijk Ruwe Simulatiedata"):
        st.dataframe(result.df.head(100))
        _download_csv(result.df, "⬇️ Download volledige simulatiedata (CSV)",
                      "JortApp_simulatiedata.csv", key=f"{key_prefix}_raw_csv")


# ── Battery-size sweep renderer ────────────────────────────────────────────────

_SWEEP_COST_COLOR = "#2a78d6"
_SWEEP_SAVINGS_COLOR = "#eb6834"


def _build_single_metric_chart(rows, metric_key, title, color, fill_rgba):
    """Line-plus-band chart for one metric: x=batterijgrootte, y=€, a band
    spanning the 80-95% efficiency range with the 90% value as the central
    line. A single series needs no legend box — the title names it."""
    by_size = {size: {} for size in SWEEP_SIZES_KWH}
    for r in rows:
        by_size[r['size_kwh']][r['efficiency']] = r

    mid = [by_size[s][0.90][metric_key] for s in SWEEP_SIZES_KWH]
    low_eff = [by_size[s][0.80][metric_key] for s in SWEEP_SIZES_KWH]
    high_eff = [by_size[s][0.95][metric_key] for s in SWEEP_SIZES_KWH]
    lower = [min(a, b) for a, b in zip(low_eff, high_eff)]
    upper = [max(a, b) for a, b in zip(low_eff, high_eff)]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=SWEEP_SIZES_KWH + SWEEP_SIZES_KWH[::-1],
        y=upper + lower[::-1],
        fill='toself', fillcolor=fill_rgba,
        line=dict(width=0), hoverinfo='skip', showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=SWEEP_SIZES_KWH, y=mid, mode='lines+markers',
        line=dict(color=color, width=2), marker=dict(size=8),
        showlegend=False,
        customdata=list(zip(low_eff, high_eff)),
        hovertemplate=(
            "€%{y:,.2f}<br>"
            "Bij 80%: €%{customdata[0]:,.2f} · Bij 95%: €%{customdata[1]:,.2f}"
            "<extra></extra>"
        ),
    ))
    fig.update_layout(
        title=title,
        xaxis=dict(title="Batterijgrootte (kWh)", tickvals=SWEEP_SIZES_KWH),
        yaxis=dict(title="€ per jaar"),
        hovermode="x unified",
    )
    return _style_figure(fig, height=550)


def _build_sweep_charts(rows, include_fixed):
    """Two stacked single-metric charts (kosten, then besparing) instead of
    both series in one plot.

    Besparing is identical with or without vaste kosten (the fixed costs are
    the same for the baseline and every battery config, so they cancel out of
    the difference) — only the kosten chart moves with the toggle."""
    cost_key = 'cost_total' if include_fixed else 'cost_variable'
    cost_suffix = "incl. vaste kosten" if include_fixed else "excl. vaste kosten"
    cost_fig = _build_single_metric_chart(
        rows, cost_key, f"Totale kosten per batterijgrootte ({cost_suffix})",
        _SWEEP_COST_COLOR, "rgba(42,120,214,0.15)"
    )
    savings_fig = _build_single_metric_chart(
        rows, 'savings_variable', "Besparing per batterijgrootte",
        _SWEEP_SAVINGS_COLOR, "rgba(235,104,52,0.15)"
    )
    return cost_fig, savings_fig


def _render_single_kostenopbouw(bd, include_fixed, cost_display):
    """Single-column Kostenopbouw table (Post / Bedrag) for one battery
    configuration — unlike _render_battery_detail's baseline-vs-simulated
    comparison, this shows just that one config's breakdown."""
    all_rows = _breakdown_row_defs(bd)
    rows = [r for r in all_rows if include_fixed or not r[4]]

    col_label, col_amount = st.columns([3, 2])
    col_label.markdown("**Post**")
    col_amount.markdown("**Bedrag**")
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

        raw = bd[key]
        contribution = -raw if is_credit else raw
        vol = bd[vol_key] if vol_key else None

        col_label, col_amount = st.columns([3, 2])
        col_label.markdown(f"{row_label} <small style='color:grey'>({tarief_str})</small>", unsafe_allow_html=True)
        col_amount.markdown(fmt_cell(contribution, vol), unsafe_allow_html=True)

    st.markdown("<hr style='margin:4px 0'>", unsafe_allow_html=True)
    col_label, col_amount = st.columns([3, 2])
    col_label.markdown("**Totaal**")
    col_amount.markdown(f"**€ {cost_display:,.2f}**")


def _render_sweep_results(sweep_data):
    rows = sweep_data['rows']
    strategy = sweep_data['strategy']

    st.header("Resultaten Overzicht: Batterijgrootte vergelijking")
    if sweep_data.get('provider_name'):
        if sweep_data.get('provider_comparison'):
            st.caption(f"📄 Grafieken en details hieronder: **{sweep_data['provider_name']}** (Leverancier 1). "
                       "Zie 'Vergelijking energieleveranciers' voor de andere contracten.")
        else:
            st.caption(f"📄 Energieleverancier: **{sweep_data['provider_name']}**")

    include_fixed_sweep = st.checkbox(
        "Vaste kosten meenemen", value=True, key="sweep_include_fixed",
        help="Vaste kosten omvatten abonnementskosten, netbeheerskosten en belastingvermindering. Zet uit om alleen het variabele deel te vergelijken. De besparing verandert niet — vaste kosten zijn voor elke batterij en de baseline gelijk."
    )
    _download_report(sweep_data, include_fixed_sweep, key="sweep_report_pdf")
    cost_baseline_display = sweep_data['cost_baseline'] if include_fixed_sweep else sweep_data['cost_baseline_variable']
    st.caption(
        f"Baseline (geen batterij): **€{cost_baseline_display:,.2f}** — "
        f"strategie: **{strategy}**"
    )

    cost_fig, savings_fig = _build_sweep_charts(rows, include_fixed_sweep)
    st.caption("Band = 80–95% rendement, lijn = 90%. Gebruik het camera-icoon rechtsboven in een grafiek om hem als PNG op te slaan.")
    col_cost, col_savings = st.columns(2)
    with col_cost:
        st.plotly_chart(cost_fig, use_container_width=True, config=PLOT_CONFIG)
    with col_savings:
        st.plotly_chart(savings_fig, use_container_width=True, config=PLOT_CONFIG)

    st.caption("⚠️ **Let op:** Deze waarden zijn schattingen gebaseerd op historische data en simulatiemodellen. De werkelijke resultaten kunnen afwijken door o.a. weersomstandigheden, batterij-degradatie en wijzigingen in markttarieven. Gebruik deze resultaten enkel ter oriëntatie.")

    if sweep_data.get('provider_comparison'):
        st.divider()
        _render_sweep_provider_comparison(sweep_data['provider_comparison'], rows, include_fixed_sweep)
        st.divider()

    with st.expander("Details per configuratie", expanded=False):
        cost_key = 'cost_total' if include_fixed_sweep else 'cost_variable'
        summary_df = _sweep_summary_dataframe(rows, include_fixed_sweep)
        st.dataframe(summary_df, use_container_width=True, hide_index=True)
        _download_csv(summary_df, "⬇️ Download tabel (CSV)", "JortApp_batterijgrootte_vergelijking.csv",
                      key="sweep_summary_csv")

        for r in rows:
            with st.expander(f"{r['size_kwh']} kWh @ {r['efficiency']:.0%} rendement", expanded=False):
                _render_single_kostenopbouw(
                    r['breakdown_simulated'], include_fixed=include_fixed_sweep,
                    cost_display=r[cost_key]
                )


# ── Provider comparison renderer ──────────────────────────────────────────────

_BASELINE_COLOR = "#9aa0a6"
_SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#8e5bd6", "#d6a12a", "#d63a6e", "#3ab8c9", "#6b7a8f"]


def _series_color(i):
    return _SERIES_COLORS[i % len(_SERIES_COLORS)]


def _build_provider_comparison_charts(comparison, battery_labels, include_fixed):
    """Grouped bars per provider: yearly cost (baseline + each battery) and savings."""
    names = _unique_labels([c['name'] for c in comparison])
    cost_suffix = "incl. vaste kosten" if include_fixed else "excl. vaste kosten"

    cost_fig = go.Figure()
    cost_fig.add_trace(go.Bar(
        x=names, y=[_provider_cost(c, include_fixed, baseline=True) for c in comparison],
        name="Zonder batterij", marker_color=_BASELINE_COLOR,
        hovertemplate="%{x}<br>Zonder batterij: €%{y:,.2f}<extra></extra>",
    ))
    savings_fig = go.Figure()
    for i, lbl in enumerate(battery_labels):
        cost_fig.add_trace(go.Bar(
            x=names, y=[_provider_cost(c['results'][i], include_fixed) for c in comparison],
            name=lbl, marker_color=_series_color(i),
            hovertemplate="%{x}<br>" + lbl + ": €%{y:,.2f}<extra></extra>",
        ))
        savings_fig.add_trace(go.Bar(
            x=names, y=[c['results'][i]['savings'] for c in comparison],
            name=lbl, marker_color=_series_color(i),
            hovertemplate="%{x}<br>" + lbl + ": €%{y:,.2f}<extra></extra>",
        ))
    for fig, title in ((cost_fig, f"Jaarkosten per leverancier ({cost_suffix})"),
                       (savings_fig, "Besparing per leverancier")):
        fig.update_layout(title=title, barmode="group", yaxis=dict(title="€ per jaar"))
    _style_figure(cost_fig, height=550)
    _style_figure(savings_fig, height=550)
    for fig in (cost_fig, savings_fig):
        fig.update_layout(legend=dict(orientation="h", x=0, y=-0.15))
    savings_fig.update_layout(showlegend=len(battery_labels) > 1)
    return cost_fig, savings_fig


def _cheapest_caption(comparison, option_labels, option_costs):
    """'Laagste jaarkosten: X — Y (€..)' over every provider × option combination.
    option_costs(entry) returns one cost per label in option_labels."""
    cost, name, lbl = _cheapest_option(comparison, option_labels, option_costs)
    return f"💡 Laagste jaarkosten: **{name}** {lbl}: **€{cost:,.2f}**"


def _render_provider_comparison(comparison, battery_labels, include_fixed):
    st.subheader("Vergelijking energieleveranciers")
    st.caption(
        "Dezelfde batterijsimulatie, doorgerekend met elk contract. De besparing geldt per leverancier: "
        "kosten zonder batterij minus kosten met batterij bij diezelfde leverancier. "
        "De aansturing van de batterij is bepaald door Leverancier 1."
    )
    st.markdown(_cheapest_caption(
        comparison, ["zonder batterij"] + [f"met {lbl}" for lbl in battery_labels],
        _all_option_costs(include_fixed),
    ))

    cost_fig, savings_fig = _build_provider_comparison_charts(comparison, battery_labels, include_fixed)
    col_cost, col_savings = st.columns(2)
    with col_cost:
        st.plotly_chart(cost_fig, use_container_width=True, config=PLOT_CONFIG, key="provider_cost_chart")
    with col_savings:
        st.plotly_chart(savings_fig, use_container_width=True, config=PLOT_CONFIG, key="provider_savings_chart")

    table_df = _provider_comparison_dataframe(comparison, battery_labels, include_fixed)
    st.dataframe(table_df, use_container_width=True, hide_index=True)
    _download_csv(table_df, "⬇️ Download leveranciersvergelijking (CSV)",
                  "JortApp_leveranciersvergelijking.csv", key="provider_comparison_csv")


def _build_sweep_provider_charts(comparison, rows, include_fixed, efficiency=0.90):
    """One line per provider at the given efficiency: yearly cost (0 kWh = no
    battery) and savings per battery size."""
    names = _unique_labels([c['name'] for c in comparison])
    idx_by_size = {r['size_kwh']: i for i, r in enumerate(rows) if r['efficiency'] == efficiency}
    sizes = [s for s in SWEEP_SIZES_KWH if s in idx_by_size]
    cost_suffix = "incl. vaste kosten" if include_fixed else "excl. vaste kosten"

    cost_fig, savings_fig = go.Figure(), go.Figure()
    for i, (name, c) in enumerate(zip(names, comparison)):
        style = dict(mode="lines+markers", name=name,
                     line=dict(color=_series_color(i), width=2), marker=dict(size=8),
                     hovertemplate=name + ": €%{y:,.2f}<extra></extra>")
        cost_fig.add_trace(go.Scatter(
            x=[0] + sizes,
            y=[_provider_cost(c, include_fixed, baseline=True)]
              + [_provider_cost(c['results'][idx_by_size[s]], include_fixed) for s in sizes],
            **style))
        savings_fig.add_trace(go.Scatter(
            x=sizes, y=[c['results'][idx_by_size[s]]['savings'] for s in sizes], **style))
    cost_fig.update_layout(
        title=f"Jaarkosten per leverancier ({cost_suffix})",
        xaxis=dict(title="Batterijgrootte (kWh, 0 = geen batterij)", tickvals=[0] + sizes),
    )
    savings_fig.update_layout(
        title="Besparing per leverancier",
        xaxis=dict(title="Batterijgrootte (kWh)", tickvals=sizes),
    )
    for fig in (cost_fig, savings_fig):
        fig.update_layout(yaxis=dict(title="€ per jaar"), hovermode="x unified")
        _style_figure(fig, height=550)
        fig.update_layout(legend=dict(orientation="h", x=0, y=-0.2))
    return cost_fig, savings_fig


def _render_sweep_provider_comparison(comparison, rows, include_fixed):
    st.subheader("Vergelijking energieleveranciers")
    st.caption(
        "Dezelfde batterijsimulaties, doorgerekend met elk contract. De grafieken tonen 90% rendement; "
        "de tabel bevat alle combinaties. De aansturing van de batterij is bepaald door Leverancier 1."
    )
    option_labels = ["zonder batterij"] + [f"met {r['size_kwh']} kWh @ {r['efficiency']:.0%}" for r in rows]
    st.markdown(_cheapest_caption(comparison, option_labels, _all_option_costs(include_fixed)))
    cost_fig, savings_fig = _build_sweep_provider_charts(comparison, rows, include_fixed)
    col_cost, col_savings = st.columns(2)
    with col_cost:
        st.plotly_chart(cost_fig, use_container_width=True, config=PLOT_CONFIG, key="sweep_provider_cost_chart")
    with col_savings:
        st.plotly_chart(savings_fig, use_container_width=True, config=PLOT_CONFIG, key="sweep_provider_savings_chart")
    with st.expander("Leveranciersvergelijking als tabel", expanded=False):
        table_df = _sweep_provider_dataframe(comparison, rows, include_fixed)
        st.dataframe(table_df, use_container_width=True, hide_index=True)
        _download_csv(table_df, "⬇️ Download leveranciersvergelijking (CSV)",
                      "JortApp_leveranciersvergelijking_batterijgrootte.csv",
                      key="sweep_provider_comparison_csv")


# ── Secrets / API Key ─────────────────────────────────────────────────────────

try:
    ENTSOE_API_KEY = st.secrets["ENTSOE_API_KEY"]
except Exception:
    ENTSOE_API_KEY = None

try:
    SMP_API_KEY_DEFAULT = st.secrets["SLIMMEMETERPORTAL_API_KEY"]
except Exception:
    SMP_API_KEY_DEFAULT = None

# Optional: privacy-safe failure alerts and agent issues (see error_reporting.py)
def _secret(name, default=None):
    try:
        return st.secrets[name]
    except Exception:
        return default


@st.cache_resource
def _alert_limiter():
    """One limiter for all sessions, so limits hold across users and reruns."""
    return error_reporting.AlertLimiter()


def _report_failure(error, source, upload=None):
    """Hand a failure to the background reporter; True if it was queued.

    Isolated from the app on purpose: everything is wrapped so that a bug in
    reporting can never break the page, and the actual triage and network
    calls run on a background worker (error_reporting.submit_report), so a
    hanging ntfy/GitHub can't block the user. Remembered per session, so
    repeated clicks keep showing the notice without queueing duplicates.
    """
    try:
        if error_reporting is None:
            return False
        key = (source, type(error).__name__, getattr(upload, "file_id", None))
        known = st.session_state.setdefault("reported_failures", {})
        if key not in known:
            try:
                ip = st.context.ip_address
            except Exception:
                ip = None
            settings = error_reporting.AlertSettings(
                webhook_url=_secret("ALERT_WEBHOOK_URL"),
                github_token=_secret("GITHUB_ISSUE_TOKEN"),
                github_repo=_secret("GITHUB_REPO", "GreenYoghurt/BattWatt"),
            )
            known[key] = error_reporting.submit_report(
                error, source=source, settings=settings, upload=upload,
                limiter=_alert_limiter(), ip=ip)
        return known[key]
    except Exception:  # noqa: BLE001
        return False


# Reporting runs in the background, so the page can't know the outcome; it
# only knows the failure was handed over (triage may still decide to ignore it).
_NOTIFIED_MSG = ("Als dit een probleem aan onze kant lijkt, wordt de beheerder van deze app "
                 "automatisch ingelicht (zonder je meetgegevens).")

# ── Page header ───────────────────────────────────────────────────────────────

st.title("🔋 JortApp: Thuisbatterij Evaluator")
st.markdown("""
Evalueer de impact van een thuisbatterij op je energierekening met de Nederlandse marktdynamiek.
Upload je **kwartiergegevens** (P1-meterdata per 15 minuten) om te beginnen.

Deze tool gaat uit van een **dynamisch energiecontract** en houdt geen rekening met de
salderingsregeling — de situatie die vanaf 2027 voor iedereen de werkelijkheid wordt.
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
                Alle uploads en berekeningen blijven binnen uw sessie. Als het inlezen van een
                bestand mislukt, ontvangt de beheerder een foutmelding met alleen de structuur
                van het bestand (kolomnamen en gemaskeerde waardepatronen, zoals <code>'-99</code>) —
                nooit uw meetwaarden, tijdstippen of bestandsnaam. Om misbruik te beperken wordt
                een onherleidbare hash van uw IP-adres tijdelijk in het geheugen bijgehouden; die
                wordt nergens opgeslagen of verstuurd. Deze app wordt gehost op
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
meter_source = st.sidebar.radio(
    "Databron",
    ["Bestand upload", "SlimmeMeterPortal API"],
    captions=[
        "Upload zelf een bestand met kwartiergegevens, geëxporteerd uit je meter-app of leveranciersportaal.",
        "Haal je kwartiergegevens automatisch op via [SlimmeMeterPortal.nl](https://www.slimmemeterportal.nl/), "
        "een dienst die je slimme meter uitleest (abonnement vereist).",
    ],
    horizontal=True,
)

uploaded_meter = None
custom_mapping = None

if meter_source == "Bestand upload":
    uploaded_meter = st.sidebar.file_uploader(
        "Upload Meter Data — kwartiergegevens (CSV of Excel)", type=["csv", "xlsx"]
    )

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
        except Exception as e:
            # Exception, not just SlimmeMeterPortalError: network failures
            # (requests.ConnectionError/Timeout) used to crash with a traceback.
            st.session_state.pop("smp_connections", None)
            st.sidebar.error(f"Fout bij ophalen aansluitingen: {e}")
            if _report_failure(e, "slimmemeterportal"):
                st.sidebar.caption(_NOTIFIED_MSG)

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
                except Exception as e:
                    st.session_state.pop("smp_meter_df", None)
                    st.session_state.pop("smp_missing_dates", None)
                    st.sidebar.error(f"Fout bij ophalen data: {e}")
                    if _report_failure(e, "slimmemeterportal"):
                        st.sidebar.caption(_NOTIFIED_MSG)
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

# 2. Batteries
st.sidebar.subheader("🔋 Batterijen")

battery_mode = st.sidebar.radio(
    "Batterijconfiguratie", ["Batterijgrootte vergelijking", "Handmatige batterijen"],
    help="**Handmatige batterijen:** stel zelf 1 of meer specifieke batterijen samen.\n\n**Batterijgrootte vergelijking:** draait automatisch 12 simulaties (4 groottes × 3 rendementen) en toont het effect van batterijgrootte op kosten en besparing."
)

if battery_mode == "Batterijgrootte vergelijking":
    sizes_str = ", ".join(f"{s} kWh" for s in SWEEP_SIZES_KWH)
    eff_str = ", ".join(f"{e:.0%}" for e in SWEEP_EFFICIENCIES)
    st.sidebar.caption(
        f"Draait automatisch **{len(SWEEP_SIZES_KWH) * len(SWEEP_EFFICIENCIES)} simulaties**: "
        f"groottes {sizes_str} × rendementen {eff_str}."
    )
else:
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
            st.text_input("Label", key=f"bat_label_{bid}")

            preset_default_idx = (_battery_preset_options.index(st.session_state[f"bat_preset_{bid}"])
                                  if st.session_state[f"bat_preset_{bid}"] in _battery_preset_options else 1)
            st.selectbox("Sjabloon", _battery_preset_options, index=preset_default_idx, key=f"bat_preset_{bid}")

            if st.session_state.get(f"bat_preset_{bid}") == "Handmatig invoeren (Custom)":
                st.number_input("Capaciteit (kWh)", value=10.0, step=0.5, key=f"bat_cap_{bid}")
                st.number_input(
                    "Max. Laadvermogen (kW)", value=None, step=0.1, key=f"bat_charge_{bid}",
                    placeholder="bijv. 3.68"
                )
                st.number_input(
                    "Max. Ontlaadvermogen (kW)", value=None, step=0.1, key=f"bat_discharge_{bid}",
                    placeholder="bijv. 3.68"
                )
                st.slider(
                    "Netto rendement gehele installatie (%)", 80, 100, 90, key=f"bat_eff_{bid}",
                    help="Rendement van de volledige installatie over een volledige laad- en ontlaadcyclus (round-trip)."
                )
                st.number_input(
                    "Standby verbruik (W)", value=10.0, step=1.0, min_value=0.0, key=f"bat_standby_{bid}",
                    help="Continu sluipverbruik van de omvormer/BMS, ook wanneer de batterij niet laadt of ontlaadt."
                )

            # Full-width delete button below the fields (see issue #43): a 🗑 in
            # a narrow side column got clipped to an unusable sliver in the sidebar.
            if len(st.session_state.battery_ids) > 1:
                if st.button("Verwijder deze batterij", icon=":material/delete:",
                             key=f"del_{bid}", width="stretch"):
                    st.session_state.battery_ids.remove(bid)
                    st.rerun()

    if st.sidebar.button("➕ Batterij toevoegen", use_container_width=True):
        new_id = st.session_state.battery_counter
        st.session_state.battery_counter += 1
        st.session_state.battery_ids.append(new_id)
        st.session_state[f"bat_label_{new_id}"] = "10 kWh"
        st.session_state[f"bat_preset_{new_id}"] = "10 kWh"
        st.rerun()

st.sidebar.divider()

# 3. Provider(s)
st.sidebar.subheader("💶 Energieleverancier")

_PROVIDER_DEFAULTS = {
    "prov_type": "Dynamisch contract",
    "prov_sub": 75.0,
    "prov_normaal": 0.28,
    "prov_dal": 0.26,
    "prov_terug": 0.09,
    "prov_buy": 0.02,
    "prov_sell": 0.02,
}


def _init_provider_state(pid, name):
    """Seed defaults for a provider's widgets. Widgets that were hidden (e.g. the
    fixed-contract fields of a dynamic provider) lose their state in Streamlit, so
    this also restores defaults when they come back."""
    st.session_state.setdefault(f"prov_name_{pid}", name)
    for field, default in _PROVIDER_DEFAULTS.items():
        st.session_state.setdefault(f"{field}_{pid}", default)


# Required choice for fixed contracts: whether the entered normaal/dal tariffs
# already include energy tax. Deliberately has no default (index=None), so the
# user has to look at their tariff card; see `_fixed_tariffs_all_in`.
_EB_INCL = "Inclusief energiebelasting"
_EB_EXCL = "Exclusief energiebelasting"


def _fixed_tariffs_all_in(pid):
    """(normaal, dal) as all-in tariffs, adding energy tax if entered without it."""
    normaal = st.session_state.get(f"prov_normaal_{pid}", _PROVIDER_DEFAULTS["prov_normaal"])
    dal = st.session_state.get(f"prov_dal_{pid}", _PROVIDER_DEFAULTS["prov_dal"])
    if st.session_state.get(f"prov_eb_{pid}") == _EB_EXCL:
        return tariff_incl_energy_tax(normaal), tariff_incl_energy_tax(dal)
    return normaal, dal


def _missing_energy_tax_choice(pid):
    return (st.session_state.get(f"prov_type_{pid}") == "Vast contract"
            and st.session_state.get(f"prov_eb_{pid}") not in (_EB_INCL, _EB_EXCL))


def _provider_from_id(pid):
    """Builds a Provider from the sidebar widgets of provider `pid`."""
    get = lambda field: st.session_state.get(f"{field}_{pid}", _PROVIDER_DEFAULTS.get(field))
    name = st.session_state.get(f"prov_name_{pid}") or "Leverancier"
    if get("prov_type") == "Vast contract":
        normaal, dal = _fixed_tariffs_all_in(pid)
        return Provider(
            name=name, subscription_cost=get("prov_sub"),
            buying_fee=0.0, selling_fee=0.0,
            net_metering=False, selling_fee_net_metering=False,
            contract_type="fixed",
            normaal_tarief=normaal, dal_tarief=dal,
            terugleververgoeding=get("prov_terug"),
        )
    return Provider(
        name=name, subscription_cost=get("prov_sub"),
        buying_fee=get("prov_buy"), selling_fee=get("prov_sell"),
        net_metering=False, selling_fee_net_metering=True,
        contract_type="dynamic",
    )


if "provider_ids" not in st.session_state:
    st.session_state.provider_ids = [0]
    st.session_state.provider_counter = 1

for pid in list(st.session_state.provider_ids):
    _init_provider_state(pid, "Mijn Leverancier" if pid == 0 else f"Leverancier {pid + 1}")
    idx_in_list = st.session_state.provider_ids.index(pid) + 1
    with st.sidebar.expander(f"Leverancier {idx_in_list}: {st.session_state[f'prov_name_{pid}']}",
                             expanded=(idx_in_list == 1)):
        st.text_input("Naam energieleverancier (voor eigen documentatie)", key=f"prov_name_{pid}")

        st.radio(
            "Type contract", ["Dynamisch contract", "Vast contract"], horizontal=True,
            key=f"prov_type_{pid}",
            help="**Dynamisch contract:** je betaalt per kwartier de actuele day-ahead marktprijs.\n\n**Vast contract:** je betaalt een vast normaal- en daltarief, en ontvangt een vaste terugleververgoeding."
        )
        st.number_input(
            "Vaste leveringskosten (€/jaar)", step=1.0, key=f"prov_sub_{pid}",
            help="Jaarlijks vast bedrag dat je leverancier rekent bovenop de variabele energiekosten."
        )

        if st.session_state[f"prov_type_{pid}"] == "Vast contract":
            st.number_input(
                "Normaaltarief (€/kWh incl. BTW)", step=0.01, format="%.4f", key=f"prov_normaal_{pid}",
                help="Leveringsprijs (incl. BTW) voor verbruik op werkdagen tussen 07:00 en 23:00. Of de energiebelasting erin zit, geef je hieronder aan."
            )
            st.number_input(
                "Daltarief (€/kWh incl. BTW)", step=0.01, format="%.4f", key=f"prov_dal_{pid}",
                help="Leveringsprijs (incl. BTW) voor verbruik op werkdagen tussen 23:00 en 07:00, en het hele weekend. Of de energiebelasting erin zit, geef je hieronder aan."
            )
            # Directly below the two tariffs it is about.
            st.radio(
                "Zijn de normaal- en daltarieven inclusief of exclusief energiebelasting? (verplicht)",
                [_EB_INCL, _EB_EXCL], index=None, key=f"prov_eb_{pid}",
                help=("Kijk op je tariefkaart of jaarafrekening. Staat de energiebelasting als **aparte regel** "
                      "vermeld (zoals bij o.a. Vandebron), dan zijn de tarieven **exclusief** energiebelasting. "
                      f"De app telt dan €{get_energy_tax_incl_vat():.4f}/kWh (incl. BTW) op bij het normaal- en "
                      "daltarief. Over teruglevering wordt geen energiebelasting geheven, dus de "
                      "terugleververgoeding blijft ongewijzigd."),
            )
            if st.session_state.get(f"prov_eb_{pid}") == _EB_EXCL:
                normaal_all_in, dal_all_in = _fixed_tariffs_all_in(pid)
                st.caption(f"Gerekend wordt met (incl. energiebelasting): normaal **€{normaal_all_in:.4f}**, "
                           f"dal **€{dal_all_in:.4f}** per kWh.")
            elif _missing_energy_tax_choice(pid):
                st.caption(":red[Kies hierboven of de tarieven inclusief of exclusief energiebelasting zijn.]")
            st.number_input(
                "Terugleververgoeding (€/kWh incl. BTW)", step=0.01, format="%.4f", key=f"prov_terug_{pid}",
                help="Vaste vergoeding per kWh die je teruglevert aan het net (geen saldering)."
            )
        else:
            st.number_input(
                "Leveranciersopslag inkoop (€/kWh incl. BTW)", format="%.4f", key=f"prov_buy_{pid}",
                help="Opslag die je leverancier rekent per kWh die je van het net afneemt, bovenop de marktprijs en energiebelasting."
            )
            st.number_input(
                "Leveranciersopslag teruglevering (€/kWh incl. BTW)", format="%.4f", key=f"prov_sell_{pid}",
                help="Kosten die je leverancier rekent per kWh die je teruglevert aan het net (een negatieve vergoeding)."
            )

        # Full-width delete button below the fields (see issue #43).
        if len(st.session_state.provider_ids) > 1:
            if st.button("Verwijder deze leverancier", icon=":material/delete:",
                         key=f"del_prov_{pid}", width="stretch"):
                st.session_state.provider_ids.remove(pid)
                st.rerun()

if st.sidebar.button("➕ Leverancier toevoegen", use_container_width=True,
                     help="Voeg een extra energiecontract toe om te vergelijken. De batterijsimulatie wordt één keer gedraaid; elke leverancier rekent daarna met dezelfde energiestromen."):
    new_id = st.session_state.provider_counter
    st.session_state.provider_counter += 1
    st.session_state.provider_ids.append(new_id)
    _init_provider_state(new_id, f"Leverancier {len(st.session_state.provider_ids)}")
    st.rerun()

providers = [_provider_from_id(pid) for pid in st.session_state.provider_ids]
# The first provider is the primary one: it drives the strategy choice and the
# MPC dispatch, and it is the one shown in the detailed Kostenopbouw.
provider = providers[0]
contract_type_choice = "Vast contract" if provider.contract_type == "fixed" else "Dynamisch contract"
if len(providers) > 1:
    st.sidebar.caption(
        f"De resultaten tonen een vergelijking van {len(providers)} leveranciers. "
        f"**Leverancier 1** bepaalt de aansturing van de batterij en de gedetailleerde kostenopbouw."
    )

st.sidebar.divider()

# 4. Strategy
st.sidebar.subheader("🎛️ Aansturing")
if contract_type_choice == "Vast contract":
    strategy_map = {
        "PV Prioriteit (Zelfconsumptie)": "PV"
    }
    strategy_help = "**PV Prioriteit:** laadt de batterij direct op met zonnestroom en ontlaadt bij verbruik. Eenvoudig en snel.\n\nBij een vast contract is 'Kosten Optimaal (MPC)' niet beschikbaar: die strategie optimaliseert op de dynamische day-ahead prijs, terwijl je bij een vast contract een vlak normaal-/daltarief betaalt."
else:
    strategy_map = {
        "PV Prioriteit (Zelfconsumptie)": "PV",
        "Kosten Optimaal (MPC)": "MPC"
    }
    strategy_help = "**PV Prioriteit:** laadt de batterij direct op met zonnestroom en ontlaadt bij verbruik. Eenvoudig en snel.\n\n**Kosten Optimaal (MPC):** optimaliseert laden/ontladen op basis van de verwachte marktprijzen over de komende 24 uur. Geeft doorgaans een hogere besparing, maar vereist een goede prijsvoorspelling."
if len(providers) > 1:
    strategy_help += (f"\n\nBij meerdere leveranciers bepaalt **Leverancier 1 ({provider.name})** de aansturing: "
                      "de batterij wordt één keer gesimuleerd en elke leverancier rekent met dezelfde energiestromen. "
                      "Kosten Optimaal (MPC) optimaliseert dus op de opslagen van Leverancier 1.")
selected_strategy = st.sidebar.selectbox(
    "Selecteer Strategie", list(strategy_map.keys()),
    help=strategy_help
)

# Price data always via API; manual upload kept in back-end only
price_source = "Automatisch (ENTSO-E API)"
uploaded_price = None

st.sidebar.divider()

# ── Simulate button ───────────────────────────────────────────────────────────

can_simulate = False
meter_data_ready = bool(uploaded_meter) if meter_source == "Bestand upload" else st.session_state.get("smp_meter_df") is not None
missing_battery_power = battery_mode == "Handmatige batterijen" and any(
    st.session_state.get(f"bat_preset_{bid}") == "Handmatig invoeren (Custom)"
    and (st.session_state.get(f"bat_charge_{bid}") is None or st.session_state.get(f"bat_discharge_{bid}") is None)
    for bid in st.session_state.get("battery_ids", [])
)
if meter_data_ready:
    can_simulate = True
    if not ENTSOE_API_KEY:
        st.sidebar.error("⚠️ Geen API Key geconfigureerd.")
        can_simulate = False
    if missing_battery_power:
        st.sidebar.error("⚠️ Vul max. laad- en ontlaadvermogen in voor handmatige batterijen.")
        can_simulate = False
    missing_eb = [i + 1 for i, pid in enumerate(st.session_state.provider_ids) if _missing_energy_tax_choice(pid)]
    if missing_eb:
        st.sidebar.error("⚠️ Geef bij " + ", ".join(f"Leverancier {i}" for i in missing_eb)
                         + " aan of de tarieven inclusief of exclusief energiebelasting zijn.")
        can_simulate = False

def _format_duration(seconds):
    """Formats a duration in seconds as e.g. '45 sec' or '2 min 15 sec'."""
    seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(seconds, 60)
    if minutes:
        return f"{minutes} min {secs} sec"
    return f"{secs} sec"


# Dutch explanation per EntsoeFetchError.kind; the raw error is only shown as
# technical detail, since it is a full request URL and unreadable for users.
PRICE_ERROR_MESSAGES = {
    "unavailable": "De ENTSO-E prijzendienst is tijdelijk niet beschikbaar (onderhoud of storing). Probeer het later opnieuw.",
    "timeout": "De ENTSO-E prijzendienst reageert te traag. Probeer het later opnieuw.",
    "connection": "Geen verbinding met de ENTSO-E prijzendienst. Controleer je internetverbinding en probeer het opnieuw.",
    "auth": "De ENTSO-E API-key is ongeldig of verlopen. Controleer de sleutel in de app-configuratie.",
    "rate_limit": "Te veel aanvragen bij ENTSO-E. Wacht een paar minuten en probeer het opnieuw.",
    "no_data": "ENTSO-E heeft geen prijzen voor deze periode. Controleer de datums in je meetdata.",
    "bad_request": "ENTSO-E heeft de aanvraag geweigerd (ongeldige periode of parameters).",
}


def _prepare_simulation_data(meter_df):
    """Fetch prices, merge with meter data, and build the no-battery baseline.
    Shared by both the manual and sweep simulation runners. Must be called
    from within an active `st.status(...)` block."""
    start_date = meter_df['timestamp'].min()
    end_date = meter_df['timestamp'].max()
    st.write(f"Marktprijzen ophalen via API ({start_date.date()} tot {end_date.date()})...")
    try:
        price_df = fetch_entsoe_prices(ENTSOE_API_KEY, start_date, end_date)
    except EntsoeFetchError as e:
        st.error("Fout bij ophalen prijzen: " + PRICE_ERROR_MESSAGES.get(
            e.kind, "Onbekende fout bij het ophalen van de marktprijzen."
        ))
        with st.expander("Technische details"):
            st.code(str(e))
        # Every ENTSO-E failure is an EntsoeFetchError, so this branch must
        # report too, or the generic branch below never runs and alerts stop.
        # Triage (error_reporting) reads the chained cause: outage/config/fix.
        if _report_failure(e, "entsoe"):
            st.caption(_NOTIFIED_MSG)
        st.stop()
    except Exception as e:
        st.error(f"Fout bij ophalen prijzen: {e}")
        if _report_failure(e, "entsoe"):
            st.caption(_NOTIFIED_MSG)
        st.stop()

    st.write("Data samenvoegen...")
    merged_df = merge_data(meter_df, price_df)
    merged_df['day_ahead_price'] = merged_df['day_ahead_price'] / 1000
    merged_df = merged_df.set_index("timestamp", drop=False)

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

    return merged_df, baseline_result, billing, cost_baseline, breakdown_baseline


def _report_context(merged_df, acknowledged_checks):
    """Settings for the PDF report that the results themselves don't carry.
    Deliberately no file name and no meter values: only the source type and
    the period covered."""
    if meter_source != "Bestand upload":
        data_source = "SlimmeMeterPortal API"
    elif custom_mapping:
        data_source = "Bestand upload (aangepaste mapping)"
    else:
        data_source = "Bestand upload (automatisch herkend formaat)"
    ts = merged_df['timestamp']
    interval_minutes = (ts.iloc[1] - ts.iloc[0]).total_seconds() / 60 if len(ts) > 1 else None
    return {
        'data_source': data_source,
        'period_start': ts.min(),
        'period_end': ts.max(),
        'n_intervals': len(ts),
        'interval_minutes': interval_minutes,
        'providers': list(providers),
        'acknowledged_checks': [(c.title, c.message) for c in acknowledged_checks],
    }


def _run_simulation(meter_df, acknowledged_checks=()):
    """Fetch prices, run baseline + all battery configs sequentially, store results."""
    with st.status("Data verwerken en simulatie uitvoeren...", expanded=True) as status:
        merged_df, baseline_result, billing, cost_baseline, breakdown_baseline = _prepare_simulation_data(meter_df)

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

        provider_comparison = None
        if len(providers) > 1:
            st.write(f"Kosten berekenen voor {len(providers)} leveranciers...")
            provider_comparison = compare_providers(
                providers, baseline_result, [r['result'] for r in all_results])

        status.update(label="Simulatie Voltooid!", state="complete", expanded=False)
        st.session_state['simulation_result'] = {
            'cost_baseline': cost_baseline,
            'breakdown_baseline': breakdown_baseline,
            'all_results': all_results,
            'strategy': strategy_map[selected_strategy],
            'provider_name': provider.name,
            'provider_comparison': provider_comparison,
            'report_context': _report_context(merged_df, acknowledged_checks),
        }
        st.session_state.pop('sweep_result', None)


def _run_sweep_simulation(meter_df, acknowledged_checks=()):
    """Fetch prices, then run the automatic battery size x efficiency grid
    (SWEEP_SIZES_KWH x SWEEP_EFFICIENCIES) and store the results."""
    with st.status("Data verwerken en sweep-simulatie uitvoeren...", expanded=True) as status:
        merged_df, baseline_result, billing, cost_baseline, breakdown_baseline = _prepare_simulation_data(meter_df)

        combos = [(size, eff) for size in SWEEP_SIZES_KWH for eff in SWEEP_EFFICIENCIES]
        rows = []
        progress_bar = st.progress(0, text="Sweep voortgang")
        sweep_start = time.time()

        for i, (size, eff) in enumerate(combos):
            st.write(f"Combinatie {i + 1}/{len(combos)}: **{size} kWh @ {eff:.0%}**...")
            battery = get_battery(SWEEP_SIZE_PRESETS[size], efficiency=eff)

            if strategy_map[selected_strategy] == "PV":
                controller = Controller_PV(battery)
            else:
                controller = Controller_MPC(battery, merged_df, provider,
                                            horizon_hours=24.0, reoptimize_every_hours=12.0)

            simulator = Simulator(battery, controller)
            result = simulator.run(merged_df)
            result.df['battery_soc_kwh'] = result.df['battery_soc'] * battery.capacity_kwh / 100

            cost_simulated = billing.calculate_bill(result)
            breakdown_simulated = billing.calculate_bill_breakdown(result)
            fixed_sim = (breakdown_simulated['abonnementskosten']
                         + breakdown_simulated['netbeheerskosten']
                         - breakdown_simulated['belastingvermindering'])
            cost_variable = cost_simulated - fixed_sim

            rows.append({
                'size_kwh': size,
                'efficiency': eff,
                'battery': battery,
                'result': result,
                'cost_total': cost_simulated,
                'cost_variable': cost_variable,
                'breakdown_simulated': breakdown_simulated,
            })
            done = i + 1
            remaining = len(combos) - done
            avg_per_combo = (time.time() - sweep_start) / done
            eta_str = f" — nog ~{_format_duration(avg_per_combo * remaining)}" if remaining else ""
            progress_bar.progress(done / len(combos), text=f"Combinatie {done}/{len(combos)}: {size} kWh @ {eff:.0%}{eta_str}")

        progress_bar.empty()

        fixed_base = (breakdown_baseline['abonnementskosten']
                      + breakdown_baseline['netbeheerskosten']
                      - breakdown_baseline['belastingvermindering'])
        cost_baseline_variable = cost_baseline - fixed_base
        for r in rows:
            r['savings_variable'] = cost_baseline_variable - r['cost_variable']

        provider_comparison = None
        if len(providers) > 1:
            st.write(f"Kosten berekenen voor {len(providers)} leveranciers...")
            provider_comparison = compare_providers(
                providers, baseline_result, [r['result'] for r in rows])

        status.update(label="Sweep Voltooid!", state="complete", expanded=False)
        st.session_state['sweep_result'] = {
            'cost_baseline': cost_baseline,
            'cost_baseline_variable': cost_baseline_variable,
            'breakdown_baseline': breakdown_baseline,
            'rows': rows,
            'strategy': strategy_map[selected_strategy],
            'provider_name': provider.name,
            'provider_comparison': provider_comparison,
            'report_context': _report_context(merged_df, acknowledged_checks),
        }
        st.session_state.pop('simulation_result', None)


def _run_selected_simulation(meter_df, acknowledged_checks=()):
    """Dispatch to the manual or sweep runner based on the current battery_mode.
    acknowledged_checks: failed data checks the user chose to run anyway (for the report)."""
    if battery_mode == "Batterijgrootte vergelijking":
        _run_sweep_simulation(meter_df, acknowledged_checks)
    else:
        _run_simulation(meter_df, acknowledged_checks)


if st.sidebar.button("🚀 Start Simulatie", use_container_width=True, type="primary", disabled=not can_simulate):
    try:
        if meter_source == "Bestand upload":
            meter_df, data_checks = SmartLoader.load_with_checks(uploaded_meter, config=custom_mapping)
        else:
            meter_df, data_checks = st.session_state["smp_meter_df"], []
    except Exception as e:
        # Only report auto-detected uploads: a failing custom mapping is a
        # user configuration issue, not a format change.
        notified = (meter_source == "Bestand upload" and not custom_mapping
                    and _report_failure(e, "upload", uploaded_meter))
        st.error(f"Fout bij inlezen meterdata: {e}")
        if notified:
            st.caption(_NOTIFIED_MSG)
        st.stop()

    failed_checks = [c for c in data_checks if not c.passed]
    if failed_checks:
        st.session_state['pending_simulation'] = {
            'meter_df': meter_df,
            'failed_checks': failed_checks,
        }
        st.session_state.pop('simulation_result', None)
        st.session_state.pop('sweep_result', None)
    else:
        st.session_state.pop('pending_simulation', None)
        _run_selected_simulation(meter_df)

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
st.sidebar.caption("[Documentatie](https://github.com/GreenYoghurt/BattWatt)")

# ── Main area ─────────────────────────────────────────────────────────────────

if 'pending_simulation' in st.session_state:
    pending = st.session_state['pending_simulation']
    for check in pending['failed_checks']:
        st.warning(f"**{check.title}**\n\n{check.message}")
    st.divider()
    if st.button("▶ Analyse alsnog uitvoeren", type="primary"):
        meter_df = st.session_state['pending_simulation']['meter_df']
        acknowledged = st.session_state['pending_simulation']['failed_checks']
        del st.session_state['pending_simulation']
        _run_selected_simulation(meter_df, acknowledged)
        st.rerun()

elif 'sweep_result' in st.session_state:
    _render_sweep_results(st.session_state['sweep_result'])

elif 'simulation_result' in st.session_state:
    res_data = st.session_state['simulation_result']
    cost_baseline = res_data['cost_baseline']
    breakdown_baseline = res_data['breakdown_baseline']
    all_results = res_data['all_results']
    strategy = res_data['strategy']

    st.header("Resultaten Overzicht")
    provider_comparison = res_data.get('provider_comparison')
    if res_data.get('provider_name'):
        if provider_comparison:
            st.caption(f"📄 Overzicht en kostenopbouw hieronder: **{res_data['provider_name']}** (Leverancier 1). "
                       "Zie 'Vergelijking energieleveranciers' voor de andere contracten.")
        else:
            st.caption(f"📄 Energieleverancier: **{res_data['provider_name']}**")
    include_fixed = st.checkbox(
        "Vaste kosten meenemen", value=True,
        help="Vaste kosten omvatten abonnementskosten, netbeheerskosten en belastingvermindering. Zet uit om alleen het variabele deel te vergelijken."
    )
    _download_report(res_data, include_fixed, key="report_pdf")

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

    if provider_comparison:
        st.divider()
        _render_provider_comparison(
            provider_comparison, _unique_labels([r['label'] for r in all_results]), include_fixed)

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
                    st.plotly_chart(create_usage_chart(plot_df, title=f"Huisverbruik & Zon vs Batterij Status (%) - {slice_title}"), use_container_width=True, config=PLOT_CONFIG, key=f"{key_prefix}_usage_{i}")
                    st.plotly_chart(create_price_chart(plot_df, title=f"Marktprijs vs Batterij SoC (kWh) - {slice_title}"), use_container_width=True, config=PLOT_CONFIG, key=f"{key_prefix}_price_{i}")
        else:
            st.plotly_chart(create_usage_chart(result.df, title="Huisverbruik & Zon vs Batterij Status (%)"), use_container_width=True, config=PLOT_CONFIG, key=f"{key_prefix}_usage")
            st.plotly_chart(create_price_chart(result.df, title="Marktprijs vs Batterij SoC (kWh)"), use_container_width=True, config=PLOT_CONFIG, key=f"{key_prefix}_price")

        if pos < len(selected_indices) - 1:
            st.divider()

    # ── Raw data ─────────────────────────────────────────────────────────────
    with st.expander("Bekijk Ruwe Simulatiedata"):
        if len(all_results) > 1:
            raw_tabs = st.tabs(pill_labels)
            for tab, res in zip(raw_tabs, all_results):
                with tab:
                    st.dataframe(res['result'].df.head(100))
                    _download_csv(res['result'].df, "⬇️ Download volledige simulatiedata (CSV)",
                                  "JortApp_simulatiedata.csv", key=f"raw_csv_{res['label']}")
        else:
            st.dataframe(all_results[0]['result'].df.head(100))
            _download_csv(all_results[0]['result'].df, "⬇️ Download volledige simulatiedata (CSV)",
                          "JortApp_simulatiedata.csv", key="raw_csv_single")

elif not uploaded_meter:
    st.info("👈 Upload je kwartiergegevens (P1-metergegevens per 15 minuten) in de zijbalk om de berekening te starten.")
    st.markdown("""
### Wat doet deze app?
Deze app rekent uit hoeveel geld je zou besparen met een thuisbatterij. Dat doet hij met
jouw eigen stroomgegevens op kwartierniveau (hoeveel je per 15 minuten verbruikt en hoeveel
zon je opwekt) en de actuele stroomprijzen.

**Zo werkt het:**
1. **Upload je kwartiergegevens** — een bestand van je slimme meter met data per 15 minuten,
   of we halen ze automatisch voor je op via SlimmeMeterPortal.nl.
2. **Kies een batterij** — een standaardmodel, of vul zelf de grootte in. Je mag ook meerdere
   batterijen tegelijk vergelijken.
3. **Vul je energieleverancier in** — wat je betaalt per maand en per kWh stroom.
4. **Kies hoe de batterij wordt aangestuurd** — dit bepaalt wanneer de batterij automatisch
   laadt en ontlaadt:
   - **PV Prioriteit**: de batterij laadt op met je eigen zonnestroom en gebruikt die
     later, zodat je zo min mogelijk stroom van het net hoeft te kopen.
   - **Kosten Optimaal**: de batterij kijkt vooruit naar de verwachte stroomprijzen en
     laadt automatisch op de goedkoopste momenten, om zo veel mogelijk geld te besparen.
5. **Klik op simuleren** — je ziet meteen hoeveel je zou besparen, met een duidelijke
   uitleg van de kosten en grafieken van je verbruik.

Je gegevens worden nergens opgeslagen — alles blijft alleen op je eigen scherm.
""")
