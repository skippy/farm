"""CLI entry point for lambing season reports.

Quick-reference dashboard commands for morning chores.

Usage::

    agriwebb-lambing season                # Current year
    agriwebb-lambing season --year 2025    # Historical
    agriwebb-lambing season --json         # Structured output
    agriwebb-lambing losses                # Current year loss breakdown
    agriwebb-lambing losses --year 2025
    agriwebb-lambing losses --json
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from agriwebb.analysis.lambing.loader import load_farm_data
from agriwebb.analysis.lambing.losses import loss_report
from agriwebb.analysis.lambing.season import lambing_season_report

# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _print_season_report(report: dict) -> None:
    """Print a human-readable lambing season report."""
    h = report["headline"]
    season = report["season"]

    print(f"\n{'=' * 56}")
    print(f"  Lambing Season {season}")
    print(f"{'=' * 56}")

    print(f"\n  Live lambs:  {h['live_lambs']:>4}   ({h['males']} males, {h['females']} females)")
    print(f"  Ewes lambed: {h['ewes_lambed']:>4}")
    print(f"  Ewes joined: {h['ewes_joined']:>4}")
    print(f"  Rate (per lambed): {h['lambing_rate_per_lambed']:.0%}")
    print(f"  Rate (per joined): {h['lambing_rate_per_joined']:.0%}")

    # Litter distribution
    dist = report["litter_distribution"]
    if dist:
        print("\n  Litter Distribution")
        print(f"  {'-' * 28}")
        labels = {1: "Singles", 2: "Twins", 3: "Triplets", 4: "Quads"}
        for size, count in sorted(dist.items()):
            label = labels.get(size, f"{size}-lambs")
            print(f"  {label:<12} {count:>4}")

    # By sire
    by_sire = report["by_sire"]
    if by_sire:
        print(f"\n  {'Sire':<16} {'Joined':>7} {'Lambed':>7} {'Live':>5} {'Lost':>5} {'Rate':>6}")
        print(f"  {'-' * 50}")
        for s in by_sire:
            rate_str = f"{s['rate']:.0%}" if s["lambed"] else "-"
            print(f"  {s['sire']:<16} {s['joined']:>7} {s['lambed']:>7} {s['live']:>5} {s['lost']:>5} {rate_str:>6}")

    # By breed
    by_breed = report["by_breed"]
    if by_breed:
        print(f"\n  {'Dam Breed':<24} {'Dams':>5} {'Live':>5} {'Lost':>5} {'Rate':>6}")
        print(f"  {'-' * 49}")
        for b in by_breed:
            rate_str = f"{b['rate']:.0%}" if b["dams"] else "-"
            print(f"  {b['breed']:<24} {b['dams']:>5} {b['live']:>5} {b['lost']:>5} {rate_str:>6}")

    # Maiden vs experienced
    mx = report["maiden_vs_experienced"]
    maiden = mx["maiden"]
    exp = mx["experienced"]
    if maiden["dams"] or exp["dams"]:
        print(f"\n  {'Experience':<16} {'Dams':>5} {'Live':>5} {'Lost':>5}")
        print(f"  {'-' * 35}")
        print(f"  {'Maiden':<16} {maiden['dams']:>5} {maiden['live']:>5} {maiden['lost']:>5}")
        print(f"  {'Experienced':<16} {exp['dams']:>5} {exp['live']:>5} {exp['lost']:>5}")

    print()


def _print_loss_report(report: dict) -> None:
    """Print a human-readable loss analysis report."""
    s = report["summary"]
    season = report["season"]

    print(f"\n{'=' * 56}")
    print(f"  Loss Analysis {season}")
    print(f"{'=' * 56}")

    print(f"\n  Total losses:   {s['total']:>4}")
    if s["prenatal"]:
        print(f"    Prenatal:     {s['prenatal']:>4}")
    if s["intrapartum"]:
        print(f"    Intrapartum:  {s['intrapartum']:>4}")
    if s["perinatal"]:
        print(f"    Perinatal:    {s['perinatal']:>4}")
    if s["stillborn"]:
        print(f"    Stillborn:    {s['stillborn']:>4}")
    if s["early_loss"]:
        print(f"    Early loss:   {s['early_loss']:>4}")
    if s["late_death"]:
        print(f"    Late loss:    {s['late_death']:>4}")
    if s["preventable"]:
        print(f"  Preventable:    {s['preventable']:>4}")

    # By dam
    by_dam = report["by_dam"]
    if by_dam:
        print(f"\n  {'Dam':<16} {'Breed':<18} {'Litter':>6} {'Lost':>5}  Category")
        print(f"  {'-' * 64}")
        for d in by_dam:
            print(f"  {d['dam']:<16} {d['breed']:<18} {d['litter_size']:>6} {d['lost']:>5}  {d['category']}")

    # By sire
    by_sire = report["by_sire"]
    if by_sire:
        print(f"\n  {'Sire':<16} {'Lambs':>6} {'Raised':>7} {'Lost':>5} {'Loss %':>7}")
        print(f"  {'-' * 45}")
        for s in by_sire:
            pct = f"{s['rate']:.0%}" if s["lambs"] else "-"
            print(f"  {s['sire']:<16} {s['lambs']:>6} {s['raised']:>7} {s['lost']:>5} {pct:>7}")

    # Year over year
    yoy = report["year_over_year"]
    if yoy:
        print(
            f"\n  {'Year':>6} {'Dams':>5} {'Born':>5} {'Raised':>7} {'Still':>6} {'Early':>6} {'Late':>5} {'Surv%':>6}"
        )
        print(f"  {'-' * 52}")
        for y in yoy:
            print(
                f"  {y['year']:>6} {y['dams']:>5} {y['born']:>5} "
                f"{y['raised']:>7} {y['stillborn']:>6} {y['early']:>6} {y['late']:>5} "
                f"{y['rate']:>5.1f}%"
            )

    print()


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------


def cmd_season(args: argparse.Namespace) -> None:
    """Run the lambing season report."""
    year = args.year or datetime.now(UTC).year
    data = load_farm_data(season=year)
    report = lambing_season_report(data)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_season_report(report)


def cmd_losses(args: argparse.Namespace) -> None:
    """Run the loss analysis report."""
    year = args.year or datetime.now(UTC).year
    data = load_farm_data(season=year)
    report = loss_report(data)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_loss_report(report)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def cli() -> None:
    """CLI entry point for agriwebb-lambing."""
    parser = argparse.ArgumentParser(
        description="Lambing season reports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  agriwebb-lambing season                Current year lambing report
  agriwebb-lambing season --year 2025    Historical report
  agriwebb-lambing season --json         JSON output
  agriwebb-lambing losses                Loss analysis for current year
  agriwebb-lambing losses --year 2025    Historical loss analysis
  agriwebb-lambing losses --json         JSON output
""",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # season
    season_parser = subparsers.add_parser("season", help="Lambing season summary report")
    season_parser.add_argument("--year", type=int, default=None, help="Lambing year (default: current)")
    season_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # losses
    losses_parser = subparsers.add_parser("losses", help="Loss analysis report")
    losses_parser.add_argument("--year", type=int, default=None, help="Lambing year (default: current)")
    losses_parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    commands = {
        "season": cmd_season,
        "losses": cmd_losses,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    cli()
