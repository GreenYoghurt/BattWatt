"""PDF report of a simulation run ("Download volledig rapport").

``build_report()`` takes the result dict app.py stores in ``st.session_state``
(``simulation_result`` for manual batteries, ``sweep_result`` for the
battery-size sweep) plus its ``report_context`` and returns the PDF as bytes.
Everything happens in memory: nothing is written to disk, in line with the
app's promise not to store data.

The charts are redrawn with matplotlib (Agg, object API so it is safe on the
background thread Streamlit uses for deferred downloads). Rendering the page's
Plotly figures would need Kaleido plus a Chromium browser, which Streamlit
Community Cloud doesn't provide. The PDF uses matplotlib's bundled DejaVu Sans
font, so € and other non-Latin-1 characters render without extra files.
"""
from datetime import datetime
from io import BytesIO
from pathlib import Path

import matplotlib
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from fpdf import FPDF
from fpdf.fonts import FontFace
import pandas as pd

from billing import fixed_costs_from_breakdown as fixed_costs
from results_tables import (
    SWEEP_SIZES_KWH, all_option_costs, breakdown_dataframe, cheapest_option,
    contract_label, provider_comparison_dataframe, provider_cost,
    sweep_provider_dataframe, sweep_summary_dataframe, unique_labels,
)

# File name of the "Download volledig rapport (PDF)" download (issue #48).
REPORT_FILENAME = "JortApp_report.pdf"

STRATEGY_LABELS = {
    "PV": "PV Prioriteit (Zelfconsumptie)",
    "MPC": "Kosten Optimaal (MPC)",
    "Price": "Prijsgestuurd (regelgebaseerd)",
}

DISCLAIMER = (
    "Let op: deze waarden zijn schattingen gebaseerd op historische data en simulatiemodellen. "
    "De werkelijke resultaten kunnen afwijken door o.a. weersomstandigheden, batterij-degradatie "
    "en wijzigingen in markttarieven. Gebruik deze resultaten enkel ter oriëntatie."
)
MPC_NOTE = (
    "Kosten Optimaal (MPC) rekent met een perfecte voorspelling van verbruik, zon-opbrengst en "
    "prijzen. De 'realistische besparing' is 80% van de geschatte besparing."
)
PRIVACY_NOTE = (
    "Dit rapport is in het geheugen van je sessie gemaakt en wordt nergens opgeslagen. "
    "Het bevat geen bestandsnaam en geen ruwe meetwaarden."
)

_FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
_FONT = "DejaVu"
_HEADER_FILL = (232, 236, 241)
_SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#8e5bd6", "#d6a12a", "#d63a6e", "#3ab8c9", "#6b7a8f"]
_BASELINE_COLOR = "#9aa0a6"
_CHART_DPI = 130
_SEASONS = {"Winter (jan)": 1, "Lente (apr)": 4, "Zomer (jul)": 7, "Herfst (okt)": 10}


# ── Formatting ────────────────────────────────────────────────────────────────

def eur(amount):
    """Same notation as the app: '€ 1,234.56', with a real minus sign."""
    return f"−€ {abs(amount):,.2f}" if amount < 0 else f"€ {amount:,.2f}"


def _cell(value):
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _date(ts):
    return pd.Timestamp(ts).strftime("%d-%m-%Y")


# ── Charts (matplotlib) ───────────────────────────────────────────────────────

def _png(fig):
    FigureCanvasAgg(fig)
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=_CHART_DPI, bbox_inches="tight")
    buf.seek(0)
    return buf


def _new_figure(height=3.6):
    fig = Figure(figsize=(9, height))
    ax = fig.add_subplot()
    ax.grid(True, color="#e5e5e5", linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    return fig, ax


def _interval_factor(df):
    """kWh per interval -> kW, as in app.get_duration_conv()."""
    if len(df) > 1:
        hours = (df['timestamp'].iloc[1] - df['timestamp'].iloc[0]).total_seconds() / 3600
        if hours > 0:
            return 1.0 / hours
    return 4.0


def usage_chart(df, title):
    """Huisverbruik & zon (kW) with the battery SoC (%) shaded behind them."""
    fig, ax = _new_figure()
    conv = _interval_factor(df)
    ax2 = ax.twinx()
    ax2.fill_between(df['timestamp'], df['battery_soc'], color="green", alpha=0.15, linewidth=0,
                     label="Batterij SoC (%)")
    ax2.set_ylim(0, 105)
    ax2.set_ylabel("Batterij SoC (%)")
    ax2.spines["top"].set_visible(False)
    ax.set_zorder(ax2.get_zorder() + 1)
    ax.patch.set_visible(False)
    ax.plot(df['timestamp'], df['teruglevering'] * conv, color="orange", linewidth=1.2, label="Zon-opbrengst (kW)")
    ax.plot(df['timestamp'], df['verbruik'] * conv, color="red", linewidth=0.9, label="Huisverbruik (kW)")
    ax.set_ylabel("Vermogen (kW)")
    ax.set_title(title, loc="left", fontsize=11)
    handles = ax2.get_legend_handles_labels()[0] + ax.get_legend_handles_labels()[0]
    labels = ax2.get_legend_handles_labels()[1] + ax.get_legend_handles_labels()[1]
    ax.legend(handles, labels, loc="upper left", ncol=3, fontsize=8, frameon=False)
    fig.autofmt_xdate()
    return _png(fig)


def price_chart(df, title):
    """Battery SoC (kWh) against the day-ahead price (€/kWh)."""
    fig, ax = _new_figure()
    soc = df['battery_soc_kwh'] if 'battery_soc_kwh' in df else df['battery_soc']
    ax.fill_between(df['timestamp'], soc, color="green", alpha=0.25, linewidth=0)
    ax.plot(df['timestamp'], soc, color="green", linewidth=1.0, label="Batterij SoC (kWh)")
    ax.set_ylabel("SoC (kWh)")
    ax2 = ax.twinx()
    ax2.plot(df['timestamp'], df['day_ahead_price'], color="#9a9a9a", linewidth=1.0, label="Marktprijs (€/kWh)")
    ax2.set_ylabel("Prijs (€/kWh)")
    ax2.spines["top"].set_visible(False)
    ax.set_title(title, loc="left", fontsize=11)
    handles = ax.get_legend_handles_labels()[0] + ax2.get_legend_handles_labels()[0]
    labels = ax.get_legend_handles_labels()[1] + ax2.get_legend_handles_labels()[1]
    ax.legend(handles, labels, loc="upper left", ncol=2, fontsize=8, frameon=False)
    fig.autofmt_xdate()
    return _png(fig)


def chart_slices(df):
    """The slices the page shows per battery: the full period, plus a week per
    season (or the first and last week) when the run spans more than 32 days."""
    slices = [("Volledige periode", df)]
    if df['timestamp'].max() - df['timestamp'].min() <= pd.Timedelta(days=32):
        return slices
    seasons = []
    for name, month in _SEASONS.items():
        mask = df['timestamp'].dt.month == month
        if mask.any():
            start = df.loc[mask, 'timestamp'].min()
            week = df[(df['timestamp'] >= start) & (df['timestamp'] < start + pd.Timedelta(days=7))]
            seasons.append((name, week))
    if not seasons:
        seasons = [("Begin van periode", df.head(7 * 24 * 4)), ("Einde van periode", df.tail(7 * 24 * 4))]
    return slices + seasons


def sweep_metric_chart(rows, metric_key, title, color):
    """Line at 90% efficiency with a band spanning 80-95%, per battery size."""
    by_size = {}
    for r in rows:
        by_size.setdefault(r['size_kwh'], {})[r['efficiency']] = r[metric_key]
    sizes = [s for s in SWEEP_SIZES_KWH if s in by_size]
    mid = [by_size[s].get(0.90) for s in sizes]
    lo = [min(by_size[s].values()) for s in sizes]
    hi = [max(by_size[s].values()) for s in sizes]
    fig, ax = _new_figure(height=3.4)
    ax.fill_between(sizes, lo, hi, color=color, alpha=0.15, linewidth=0, label="80–95% rendement")
    ax.plot(sizes, mid, color=color, marker="o", linewidth=2, label="90% rendement")
    ax.set_xticks(sizes)
    ax.set_xlabel("Batterijgrootte (kWh)")
    ax.set_ylabel("€ per jaar")
    ax.set_title(title, loc="left", fontsize=11)
    ax.legend(loc="best", fontsize=8, frameon=False)
    return _png(fig)


def provider_bar_charts(comparison, battery_labels, include_fixed):
    """Grouped bars per provider: yearly cost (baseline + each battery) and savings."""
    names = unique_labels([c['name'] for c in comparison])
    x = list(range(len(names)))
    suffix = "incl. vaste kosten" if include_fixed else "excl. vaste kosten"

    def grouped(ax, series):
        width = 0.8 / len(series)
        for i, (label, values, color) in enumerate(series):
            offset = (i - (len(series) - 1) / 2) * width
            ax.bar([p + offset for p in x], values, width=width, label=label, color=color)
        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=15 if len(names) > 3 else 0, ha="right" if len(names) > 3 else "center")
        ax.set_ylabel("€ per jaar")
        ax.legend(fontsize=8, frameon=False, ncol=min(4, len(series)), loc="upper left",
                  bbox_to_anchor=(0, -0.12))

    fig, ax = _new_figure(height=3.6)
    cost_series = [("Zonder batterij", [provider_cost(c, include_fixed, baseline=True) for c in comparison],
                    _BASELINE_COLOR)]
    cost_series += [(lbl, [provider_cost(c['results'][i], include_fixed) for c in comparison],
                     _SERIES_COLORS[i % len(_SERIES_COLORS)]) for i, lbl in enumerate(battery_labels)]
    grouped(ax, cost_series)
    ax.set_title(f"Jaarkosten per leverancier ({suffix})", loc="left", fontsize=11)
    cost_png = _png(fig)

    fig, ax = _new_figure(height=3.6)
    grouped(ax, [(lbl, [c['results'][i]['savings'] for c in comparison], _SERIES_COLORS[i % len(_SERIES_COLORS)])
                 for i, lbl in enumerate(battery_labels)])
    ax.set_title("Besparing per leverancier", loc="left", fontsize=11)
    return cost_png, _png(fig)


def sweep_provider_charts(comparison, rows, include_fixed, efficiency=0.90):
    """One line per provider at 90% efficiency: cost (0 kWh = no battery) and savings."""
    names = unique_labels([c['name'] for c in comparison])
    idx_by_size = {r['size_kwh']: i for i, r in enumerate(rows) if r['efficiency'] == efficiency}
    sizes = [s for s in SWEEP_SIZES_KWH if s in idx_by_size]
    suffix = "incl. vaste kosten" if include_fixed else "excl. vaste kosten"

    cost_fig, cost_ax = _new_figure(height=3.4)
    sav_fig, sav_ax = _new_figure(height=3.4)
    for i, (name, c) in enumerate(zip(names, comparison)):
        color = _SERIES_COLORS[i % len(_SERIES_COLORS)]
        cost_ax.plot([0] + sizes,
                     [provider_cost(c, include_fixed, baseline=True)]
                     + [provider_cost(c['results'][idx_by_size[s]], include_fixed) for s in sizes],
                     marker="o", color=color, label=name)
        sav_ax.plot(sizes, [c['results'][idx_by_size[s]]['savings'] for s in sizes],
                    marker="o", color=color, label=name)
    cost_ax.set_xticks([0] + sizes)
    cost_ax.set_xlabel("Batterijgrootte (kWh, 0 = geen batterij)")
    cost_ax.set_title(f"Jaarkosten per leverancier ({suffix}, 90% rendement)", loc="left", fontsize=11)
    sav_ax.set_xticks(sizes)
    sav_ax.set_xlabel("Batterijgrootte (kWh)")
    sav_ax.set_title("Besparing per leverancier (90% rendement)", loc="left", fontsize=11)
    for ax in (cost_ax, sav_ax):
        ax.set_ylabel("€ per jaar")
        ax.legend(fontsize=8, frameon=False)
    return _png(cost_fig), _png(sav_fig)


# ── PDF document ──────────────────────────────────────────────────────────────

class _ReportPDF(FPDF):
    def __init__(self, generated_at):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.generated_at = generated_at
        self.add_font(_FONT, "", str(_FONT_DIR / "DejaVuSans.ttf"))
        self.add_font(_FONT, "B", str(_FONT_DIR / "DejaVuSans-Bold.ttf"))
        self.add_font(_FONT, "I", str(_FONT_DIR / "DejaVuSans-Oblique.ttf"))
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(15, 15, 15)
        self.set_title("BattWatt simulatierapport")
        self.set_creator("BattWatt - Thuisbatterij Evaluator")
        self.set_lang("nl")

    def footer(self):
        self.set_y(-12)
        self.set_font(_FONT, "", 8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 5, f"BattWatt simulatierapport · {self.generated_at:%d-%m-%Y %H:%M}", align="L")
        self.cell(0, 5, f"Pagina {self.page_no()}/{{nb}}", align="R")
        self.set_text_color(0, 0, 0)

    # Building blocks
    def h1(self, text):
        self.set_font(_FONT, "B", 18)
        self.multi_cell(0, 9, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def h2(self, text):
        if self.get_y() > self.h - 50:
            self.add_page()
        self.ln(3)
        self.set_font(_FONT, "B", 13)
        self.set_text_color(30, 60, 110)
        self.multi_cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(1)

    def h3(self, text):
        if self.get_y() > self.h - 40:
            self.add_page()
        self.ln(1)
        self.set_font(_FONT, "B", 11)
        self.multi_cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")

    def para(self, text, size=9.5, style="", color=(0, 0, 0)):
        self.set_font(_FONT, style, size)
        self.set_text_color(*color)
        self.multi_cell(0, size * 0.5, text, align="L", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(1)

    def key_values(self, pairs):
        self.set_font(_FONT, "", 9.5)
        with self.table(col_widths=(55, 125), first_row_as_headings=False, line_height=5.5,
                        borders_layout="HORIZONTAL_LINES", padding=1) as table:
            for key, value in pairs:
                row = table.row()
                row.cell(key, style=FontFace(emphasis="BOLD"))
                row.cell(str(value))
        self.ln(2)

    def data_table(self, df, numeric_align="RIGHT"):
        """A DataFrame as a table; font shrinks with the number of columns."""
        n_cols = len(df.columns)
        size = 9 if n_cols <= 4 else 8 if n_cols <= 6 else 7 if n_cols <= 8 else 6
        self.set_font(_FONT, "", size)
        numeric = [pd.api.types.is_numeric_dtype(df[c]) for c in df.columns]
        widths = []
        for c in df.columns:
            # Never narrower than the longest word (no 'Dynamisc-h' breaks),
            # never wider than needed for the longest cell, capped for long text.
            self.set_font(_FONT, "B", size)
            longest_word = max(self.get_string_width(w) for w in str(c).split())
            self.set_font(_FONT, "", size)
            cells = [_cell(v) for v in df[c]]
            longest_word = max([longest_word] + [self.get_string_width(w) for v in cells for w in v.split()])
            longest_cell = max([self.get_string_width(str(c)) / 2] + [self.get_string_width(v) for v in cells])
            widths.append(max(longest_word, min(longest_cell, 70)) + 3)
        aligns = [numeric_align if is_num else "LEFT" for is_num in numeric]
        with self.table(col_widths=widths, text_align=aligns, line_height=size * 0.5 + 1,
                        headings_style=FontFace(emphasis="BOLD", fill_color=_HEADER_FILL),
                        borders_layout="SINGLE_TOP_LINE", padding=1) as table:
            header = table.row()
            for c in df.columns:
                header.cell(str(c))
            for _, values in df.iterrows():
                row = table.row()
                for v in values:
                    row.cell(_cell(v))
        self.ln(2)

    def chart(self, png, width=None):
        width = width or self.epw
        # Each chart is about 9 x 3.6 inch; keep it together on one page.
        height = width * 0.42
        if self.get_y() + height > self.h - self.b_margin:
            self.add_page()
        self.image(png, w=width)
        self.ln(2)


# ── Sections ──────────────────────────────────────────────────────────────────

def _provider_rows(providers):
    rows = []
    for i, p in enumerate(providers, start=1):
        if p.contract_type == "fixed":
            tarieven = (f"normaal € {p.normaal_tarief:.4f}/kWh, dal € {p.dal_tarief:.4f}/kWh, "
                        f"terugleververgoeding € {p.terugleververgoeding:.4f}/kWh")
        else:
            tarieven = (f"opslag inkoop € {p.buying_fee:.4f}/kWh, "
                        f"opslag teruglevering € {p.selling_fee:.4f}/kWh")
        rows.append({"#": str(i), "Naam": p.name, "Contract": contract_label(p.contract_type),
                     "Vaste leveringskosten": f"€ {p.subscription_cost:.2f}/jaar",
                     "Tarieven (incl. BTW)": tarieven})
    return pd.DataFrame(rows)


def _battery_rows(batteries):
    return pd.DataFrame([{
        "Batterij": label,
        "Capaciteit (kWh)": f"{b.capacity_kwh:g}",
        "Laadvermogen (kW)": f"{b.max_charge_kw:g}",
        "Ontlaadvermogen (kW)": f"{b.max_discharge_kw:g}",
        "Rendement": f"{b.efficiency:.0%}",
        "Standby (W)": f"{b.standby_power_w:g}",
    } for label, b in batteries])


def _settings_section(pdf, data, context, include_fixed, battery_mode):
    pdf.h2("1. Simulatie-instellingen")
    start, end = context.get('period_start'), context.get('period_end')
    pairs = [("Databron", context.get('data_source', "Onbekend"))]
    if start is not None and end is not None:
        days = (pd.Timestamp(end).normalize() - pd.Timestamp(start).normalize()).days + 1
        pairs.append(("Periode", f"{_date(start)} t/m {_date(end)} ({days} dagen)"))
    if context.get('n_intervals'):
        resolution = context.get('interval_minutes')
        pairs.append(("Meetintervallen", f"{context['n_intervals']:,}"
                      + (f" van {resolution:g} minuten" if resolution else "")))
    pairs += [
        ("Batterijconfiguratie", battery_mode),
        ("Aansturing", STRATEGY_LABELS.get(data['strategy'], data['strategy'])),
        ("Vaste kosten meegenomen", "ja" if include_fixed else "nee"),
        ("Marktprijzen", "Day-ahead prijzen NL (ENTSO-E)"),
    ]
    pdf.key_values(pairs)

    pdf.h3("Batterijen")
    if 'rows' in data:
        sizes = sorted({r['size_kwh'] for r in data['rows']})
        effs = sorted({r['efficiency'] for r in data['rows']})
        pdf.para(f"Automatische vergelijking van {len(sizes)} groottes × {len(effs)} rendementen "
                 f"({len(data['rows'])} simulaties). Rendementen: "
                 + ", ".join(f"{e:.0%}" for e in effs) + ".")
        first = {}
        for r in data['rows']:
            first.setdefault(r['size_kwh'], r['battery'])
        df = _battery_rows([(f"{s} kWh", first[s]) for s in sizes]).drop(columns="Rendement")
        pdf.data_table(df)
    else:
        pdf.data_table(_battery_rows([(r['label'], r['battery']) for r in data['all_results']]))

    pdf.h3("Energieleverancier(s)")
    providers = context.get('providers') or []
    if providers:
        if len(providers) > 1:
            pdf.para("Leverancier 1 bepaalt de aansturing van de batterij en de gedetailleerde kostenopbouw.",
                     size=8.5, color=(90, 90, 90))
        pdf.data_table(_provider_rows(providers))
    elif data.get('provider_name'):
        pdf.para(data['provider_name'])

    checks = context.get('acknowledged_checks') or []
    pdf.h3("Datakwaliteit")
    if checks:
        pdf.para("Je hebt de volgende waarschuwingen gezien en de analyse toch laten uitvoeren:", size=9)
        for title, message in checks:
            pdf.para(f"• {title}: {message}", size=9)
    else:
        pdf.para("Geen waarschuwingen bij de controle van de meetdata.", size=9)


def _manual_sections(pdf, data, include_fixed):
    all_results = data['all_results']
    bd_base = data['breakdown_baseline']
    cost_baseline = data['cost_baseline']
    display_baseline = cost_baseline - fixed_costs(bd_base) if (not include_fixed and bd_base) else cost_baseline

    def display_cost(res):
        bd = res['breakdown_simulated']
        return res['cost_simulated'] - fixed_costs(bd) if (not include_fixed and bd) else res['cost_simulated']

    display_costs = [display_cost(r) for r in all_results]
    labels = unique_labels([r['label'] for r in all_results])
    mpc = data['strategy'] == "MPC"

    pdf.h2("2. Resultaten overzicht")
    if data.get('provider_comparison'):
        pdf.para(f"Overzicht en kostenopbouw voor {data.get('provider_name', 'Leverancier 1')} (Leverancier 1).",
                 size=9, color=(90, 90, 90))
    summary = [{"Optie": "Zonder batterij (baseline)", "Jaarkosten": eur(display_baseline),
                "Besparing": "—", "Batterij cycli": "—", **({"Realistische besparing (80%)": "—"} if mpc else {})}]
    for lbl, res, dc in zip(labels, all_results, display_costs):
        savings = display_baseline - dc
        row = {"Optie": lbl, "Jaarkosten": eur(dc), "Besparing": eur(savings),
               "Batterij cycli": f"{getattr(res['result'], 'total_cycles', 0.0):.1f}"}
        if mpc:
            row["Realistische besparing (80%)"] = eur(savings * 0.8)
        summary.append(row)
    pdf.data_table(pd.DataFrame(summary))
    if mpc:
        pdf.para(MPC_NOTE, size=8.5, color=(90, 90, 90))

    if bd_base and all(r['breakdown_simulated'] for r in all_results):
        pdf.h3("Kostenopbouw vergelijking")
        pdf.para("Bedragen in € per jaar; opbrengsten en kortingen zijn negatief.", size=8.5, color=(90, 90, 90))
        pdf.data_table(breakdown_dataframe(all_results, bd_base, display_baseline, display_costs, include_fixed))

    comparison = data.get('provider_comparison')
    if comparison:
        option_labels = ["zonder batterij"] + [f"met {lbl}" for lbl in labels]
        _provider_section(pdf, comparison, option_labels, include_fixed,
                          provider_comparison_dataframe(comparison, labels, include_fixed),
                          provider_bar_charts(comparison, labels, include_fixed),
                          "Dezelfde batterijsimulatie, doorgerekend met elk contract. De besparing geldt per "
                          "leverancier: kosten zonder batterij minus kosten met batterij bij diezelfde leverancier. "
                          "De aansturing van de batterij is bepaald door Leverancier 1.")

    pdf.add_page()
    pdf.h2(f"{4 if comparison else 3}. Energieflow per batterij")
    for lbl, res in zip(labels, all_results):
        if len(all_results) > 1:
            pdf.h3(lbl)
        for slice_title, df in chart_slices(res['result'].df):
            if df.empty:
                continue
            pdf.chart(usage_chart(df, f"Huisverbruik & zon vs batterij status (%) – {slice_title}"))
            pdf.chart(price_chart(df, f"Marktprijs vs batterij SoC (kWh) – {slice_title}"))


def _provider_section(pdf, comparison, option_labels, include_fixed, table_df, charts, intro):
    pdf.h2("3. Vergelijking energieleveranciers")
    pdf.para(intro, size=9, color=(90, 90, 90))
    cost, name, lbl = cheapest_option(comparison, option_labels, all_option_costs(include_fixed))
    pdf.para(f"Laagste jaarkosten: {name} {lbl}: {eur(cost)}", style="B")
    pdf.data_table(table_df)
    for png in charts:
        pdf.chart(png)


def _sweep_sections(pdf, data, include_fixed):
    rows = data['rows']
    baseline = data['cost_baseline'] if include_fixed else data['cost_baseline_variable']
    cost_key = 'cost_total' if include_fixed else 'cost_variable'

    pdf.h2("2. Resultaten overzicht: batterijgrootte vergelijking")
    if data.get('provider_comparison'):
        pdf.para(f"Grafieken en details voor {data.get('provider_name', 'Leverancier 1')} (Leverancier 1).",
                 size=9, color=(90, 90, 90))
    best = max(rows, key=lambda r: r['savings_variable'])
    pdf.key_values([
        ("Baseline (geen batterij)", eur(baseline)),
        ("Hoogste besparing", f"{eur(best['savings_variable'])} met {best['size_kwh']} kWh "
                              f"@ {best['efficiency']:.0%} rendement"),
    ])
    pdf.h3("Details per configuratie")
    pdf.data_table(sweep_summary_dataframe(rows, include_fixed))
    suffix = "incl. vaste kosten" if include_fixed else "excl. vaste kosten"
    pdf.chart(sweep_metric_chart(rows, cost_key, f"Totale kosten per batterijgrootte ({suffix})", "#2a78d6"))
    pdf.chart(sweep_metric_chart(rows, 'savings_variable', "Besparing per batterijgrootte", "#eb6834"))
    pdf.para("Band = 80–95% rendement, lijn = 90%. De besparing is gelijk met of zonder vaste kosten.",
             size=8.5, color=(90, 90, 90))

    comparison = data.get('provider_comparison')
    if comparison:
        option_labels = ["zonder batterij"] + [f"met {r['size_kwh']} kWh @ {r['efficiency']:.0%}" for r in rows]
        _provider_section(pdf, comparison, option_labels, include_fixed,
                          sweep_provider_dataframe(comparison, rows, include_fixed),
                          sweep_provider_charts(comparison, rows, include_fixed),
                          "Dezelfde batterijsimulaties, doorgerekend met elk contract. De grafieken tonen 90% "
                          "rendement; de tabel bevat alle combinaties. De aansturing van de batterij is bepaald "
                          "door Leverancier 1.")


def build_report(data, include_fixed=True, generated_at=None):
    """PDF bytes for a stored result: ``simulation_result`` (has 'all_results')
    or ``sweep_result`` (has 'rows'). ``data['report_context']`` carries the
    settings that aren't part of the results themselves (data source, period,
    providers, acknowledged data-quality warnings)."""
    generated_at = generated_at or datetime.now()
    context = data.get('report_context') or {}
    is_sweep = 'rows' in data
    battery_mode = "Batterijgrootte vergelijking" if is_sweep else "Handmatige batterijen"

    pdf = _ReportPDF(generated_at)
    pdf.add_page()
    pdf.h1("BattWatt simulatierapport")
    pdf.para(f"Thuisbatterij Evaluator · gegenereerd op {generated_at:%d-%m-%Y} om {generated_at:%H:%M}",
             size=10, color=(90, 90, 90))
    pdf.para(DISCLAIMER, size=9, style="I", color=(150, 80, 0))

    _settings_section(pdf, data, context, include_fixed, battery_mode)
    if is_sweep:
        _sweep_sections(pdf, data, include_fixed)
    else:
        _manual_sections(pdf, data, include_fixed)

    if pdf.get_y() > pdf.h - 65:  # keep the closing section together
        pdf.add_page()
    pdf.h2("Over dit rapport")
    pdf.para(DISCLAIMER, size=9)
    pdf.para(PRIVACY_NOTE, size=9)
    pdf.para("Meer uitleg over de berekening: https://github.com/GreenYoghurt/BattWatt/tree/main/docs", size=9)
    return bytes(pdf.output())
