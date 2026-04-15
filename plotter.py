import pandas as pd
import matplotlib.pyplot as plt

# Maximaal aantal punten om direct te plotten
# (als er meer zijn, wordt er eenvoudig gedownsampeld)
MAX_POINTS = 2000

def show() -> None:
    plt.show()

def downsample_for_plot(df: pd.DataFrame, max_points: int) -> pd.DataFrame:
    """
    Eenvoudige downsampling: neem elke n-de rij als er te veel punten zijn.
    """
    if len(df) <= max_points:
        return df
    step = max(1, len(df) // max_points)
    return df.iloc[::step, :]


def plot_usage_and_price(df: pd.DataFrame) -> None:
    """
    Maak een plot met:
      - verbruik en teruglevering op linker Y-as
      - prijs op rechter Y-as
    """
    #plot_df = downsample_for_plot(df, MAX_POINTS)
    plot_df = df  # Voor nu zonder downsampling

    fig, ax1 = plt.subplots(figsize=(12, 5))

    # Verbruik / Teruglevering
    ax1.plot(plot_df["timestamp"], plot_df["verbruik"], label="Verbruik (kWh)", marker='.')
    ax1.plot(plot_df["timestamp"], plot_df["teruglevering"], label="Teruglevering (kWh)", marker='.')
    ax1.set_xlabel("Tijd")
    ax1.set_ylabel("Verbruik / Teruglevering (kWh)")

    # Prijs (tweede as)
    colors = plt.rcParams['axes.prop_cycle'].by_key()['color']
    c1 = colors[len(ax1.get_lines()) % len(colors)]
    ax2 = ax1.twinx()
    ax2.plot(plot_df["timestamp"], plot_df["day_ahead_price"], label="Prijs (EUR/kWh)", color=c1, marker='.')
    ax2.plot(plot_df["timestamp"], plot_df["prijs_inkoop"], label="Prijs inkoop (EUR/kWh)", color=c1, marker='.', linestyle='--')
    ax2.plot(plot_df["timestamp"], plot_df["prijs_verkoop"], label="Prijs teruglevering (EUR/kWh)", color=c1, marker='.', linestyle=':')
    ax2.set_ylabel("Prijs (EUR/MWh)")

    # Gecombineerde legenda
    lines_1, labels_1 = ax1.get_legend_handles_labels()
    lines_2, labels_2 = ax2.get_legend_handles_labels()
    ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc="upper right")

    fig.autofmt_xdate()
    plt.title("Verbruik / Teruglevering en Day-ahead prijs 2025")
    plt.tight_layout()
    #plt.show()


def plot_battery_effect(df: pd.DataFrame) -> None:
    plot_df = df
    fig, ax1 = plt.subplots(figsize=(12, 5))
    ax1.plot(plot_df["timestamp"], plot_df["verbruik"]-plot_df["teruglevering"], label="netto verbruik (kWh)", marker='.')
    ax1.plot(plot_df["timestamp"], plot_df["adjusted_consumption"]-plot_df["adjusted_production"], label="netto verbruik na batterij (kWh)", marker='.')
    ax1.set_xlabel("Tijd")
    ax1.set_ylabel("Verbruik / Teruglevering (kWh)")
    colors = plt.rcParams['axes.prop_cycle'].by_key()['color']
    c1 = colors[len(ax1.get_lines()) % len(colors)]
    ax2 = ax1.twinx()
    ax2.plot(plot_df["timestamp"], plot_df["battery_soc"], label="Battery SoC (%)", color=c1, linestyle='--')
    ax2.set_ylabel("Battery SoC (%)")


    # Gecombineerde legenda
    lines_1, labels_1 = ax1.get_legend_handles_labels()
    lines_2, labels_2 = ax2.get_legend_handles_labels()
    ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc="upper right")

    fig.autofmt_xdate()
    plt.title("Impact van batterij")
    plt.tight_layout()