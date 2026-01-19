#!/usr/bin/env python3
"""
Generate carbon balance summary visualization.
"""

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from agriwebb.core import get_cache_dir


def create_summary_chart():
    """Create a 4-panel summary visualization of carbon balance."""

    # Load the report data
    with open(get_cache_dir() / "carbon_balance_report.json") as f:
        report = json.load(f)

    # Extract key values
    complete = report["net_carbon_balance"]["complete_with_all_emissions"]

    # Sources (positive)
    sequestration_mid = complete["pasture_sequestration_t_co2"]["mid"]
    rotational_bonus = complete["rotational_grazing_enhancement_t_co2"]
    manure_c = complete["manure_c_to_soil_t_co2"]
    avoided_fert = complete["avoided_fertilizer_t_co2eq"]

    # Sinks (negative - shown as positive for chart)
    methane = abs(complete["livestock_methane_t_co2eq"])
    n2o = abs(complete["livestock_n2o_t_co2eq"])
    exports = abs(complete["carbon_exports_t_co2"])

    # Net balance
    net_low = complete["net_t_co2_year"]["low"]
    net_mid = complete["net_t_co2_year"]["mid"]
    net_high = complete["net_t_co2_year"]["high"]

    # Create figure with 4 subplots
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("San Juan Island Sheep Farm - Carbon Balance Summary", fontsize=14, fontweight="bold")

    # Color scheme
    colors_pos = ["#2ecc71", "#27ae60", "#1abc9c", "#16a085"]  # Greens
    colors_neg = ["#e74c3c", "#c0392b", "#d35400"]  # Reds

    # Panel 1: Carbon Sources (Sequestration)
    ax1 = axes[0, 0]
    sources = [sequestration_mid, rotational_bonus, manure_c, avoided_fert]
    source_labels = [
        f"Pasture Sequestration\n({sequestration_mid:.0f} t)",
        f"Rotational Grazing\nEnhancement ({rotational_bonus:.0f} t)",
        f"Manure C to Soil\n({manure_c:.0f} t)",
        f"Avoided Fertilizer\n({avoided_fert:.0f} t)"
    ]

    wedges, texts, autotexts = ax1.pie(
        sources,
        labels=source_labels,
        colors=colors_pos,
        autopct=lambda pct: f"{pct:.1f}%",
        pctdistance=0.75,
        startangle=90
    )
    ax1.set_title(f"Carbon Sinks: {sum(sources):.0f} t CO2/yr", fontsize=11, fontweight="bold")

    # Panel 2: Carbon Emissions
    ax2 = axes[0, 1]
    emissions = [methane, n2o, exports]
    emission_labels = [
        f"Methane (CH4)\n{methane:.1f} t CO2eq",
        f"Nitrous Oxide (N2O)\n{n2o:.1f} t CO2eq",
        f"Carbon Exports\n{exports:.1f} t CO2"
    ]

    wedges2, texts2, autotexts2 = ax2.pie(
        emissions,
        labels=emission_labels,
        colors=colors_neg,
        autopct=lambda pct: f"{pct:.1f}%",
        pctdistance=0.7,
        startangle=90
    )
    ax2.set_title(f"Carbon Sources (Emissions): {sum(emissions):.1f} t CO2eq/yr", fontsize=11, fontweight="bold")

    # Panel 3: Net Balance Waterfall
    ax3 = axes[1, 0]

    categories = ["Sequestration", "Rotational\nGrazing", "Manure C", "Avoided\nFertilizer",
                  "Methane", "N2O", "Exports", "NET"]
    values = [sequestration_mid, rotational_bonus, manure_c, avoided_fert,
              -methane, -n2o, -exports, net_mid]

    # Calculate cumulative for waterfall
    cumulative = np.zeros(len(values))
    cumulative[0] = values[0]
    for i in range(1, len(values) - 1):
        cumulative[i] = cumulative[i-1] + values[i]
    cumulative[-1] = net_mid

    # Starting points for bars
    starts = np.zeros(len(values))
    for i in range(1, len(values) - 1):
        if values[i] >= 0:
            starts[i] = cumulative[i-1]
        else:
            starts[i] = cumulative[i]
    starts[-1] = 0  # Net bar starts from 0

    bar_colors = []
    for i, v in enumerate(values):
        if i == len(values) - 1:  # Net bar
            bar_colors.append("#3498db")
        elif v >= 0:
            bar_colors.append("#2ecc71")
        else:
            bar_colors.append("#e74c3c")

    bars = ax3.bar(categories, [abs(v) for v in values], bottom=starts, color=bar_colors, edgecolor="white", linewidth=1)

    # Add value labels on bars
    for i, (bar, val) in enumerate(zip(bars, values)):
        height = bar.get_height()
        y_pos = bar.get_y() + height / 2
        label = f"+{val:.0f}" if val > 0 else f"{val:.0f}"
        ax3.annotate(label, xy=(bar.get_x() + bar.get_width()/2, y_pos),
                    ha="center", va="center", fontsize=9, fontweight="bold", color="white")

    ax3.axhline(y=0, color="black", linewidth=0.5)
    ax3.set_ylabel("t CO2/yr")
    ax3.set_title("Carbon Balance Waterfall (Mid Scenario)", fontsize=11, fontweight="bold")
    ax3.set_ylim(-50, 450)

    # Panel 4: Scenario Comparison
    ax4 = axes[1, 1]

    scenarios = ["Low", "Mid", "High"]
    net_values = [net_low, net_mid, net_high]
    emission_value = methane + n2o + exports  # Same for all scenarios

    x = np.arange(len(scenarios))
    width = 0.35

    bars1 = ax4.bar(x - width/2, [complete["pasture_sequestration_t_co2"]["low"] + rotational_bonus + manure_c + avoided_fert,
                                   complete["pasture_sequestration_t_co2"]["mid"] + rotational_bonus + manure_c + avoided_fert,
                                   complete["pasture_sequestration_t_co2"]["high"] + rotational_bonus + manure_c + avoided_fert],
                    width, label="C Sinks", color="#2ecc71")
    bars2 = ax4.bar(x + width/2, [emission_value] * 3, width, label="Emissions", color="#e74c3c")

    # Add net values as text
    for i, (net, scenario) in enumerate(zip(net_values, scenarios)):
        ax4.annotate(f"Net: +{net:.0f}", xy=(i, max(bars1[i].get_height(), bars2[i].get_height()) + 20),
                    ha="center", fontsize=10, fontweight="bold", color="#3498db")

    ax4.set_ylabel("t CO2eq/yr")
    ax4.set_xticks(x)
    ax4.set_xticklabels(scenarios)
    ax4.legend(loc="upper right")
    ax4.set_title("Scenario Comparison: All Scenarios Carbon Positive", fontsize=11, fontweight="bold")

    # Add farm info text box
    farm_info = (
        f"Farm: 117.3 ha pasture, 109 sheep\n"
        f"Stocking rate: 0.93 sheep/ha\n"
        f"Emissions breakdown:\n"
        f"  CH4: {methane:.1f} t CO2eq (lactation +5.6%)\n"
        f"  N2O: {n2o:.1f} t CO2eq (manure)\n"
        f"Net balance: +{net_mid:.0f} t CO2/yr (mid)"
    )

    props = dict(boxstyle="round,pad=0.5", facecolor="wheat", alpha=0.8)
    fig.text(0.98, 0.02, farm_info, fontsize=9, verticalalignment="bottom",
             horizontalalignment="right", bbox=props, family="monospace")

    plt.tight_layout(rect=[0, 0.08, 1, 0.95])

    # Save
    output_path = get_cache_dir() / "carbon_balance_summary.png"
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"Saved: {output_path}")

    plt.close()
    return output_path


if __name__ == "__main__":
    create_summary_chart()
