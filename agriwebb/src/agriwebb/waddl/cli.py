"""CLI for resolving vial tokens into WADDL submission fields.

agriwebb-waddl resolve 0693 7854 Camas          # human-readable table
agriwebb-waddl resolve 0693 7854 --json         # machine-readable (for the fill step)
agriwebb-waddl resolve 2870 --as-of 2026-07-20  # age relative to a given date
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import date

from agriwebb.waddl.resolver import ResolvedAnimal, resolve


def _parse_as_of(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"error: --as-of must be YYYY-MM-DD, got {value!r}") from None


def _print_table(results: list[ResolvedAnimal]) -> None:
    for r in results:
        if not r.found:
            print(f"\n[{r.token}]  *** NOT FOUND ***  ({'; '.join(r.warnings)})")
            continue
        print(f"\n[{r.token}] matched by {r.matched_by}  ({r.name})")
        print(f"  ID       : {r.id_field}")
        print(f"  Taxon    : {r.taxon}")
        print(f"  Sex      : {r.sex_code}")
        print(f"  Age      : {f'{r.age} {r.age_unit}' if r.age is not None else '(unknown - set manually)'}")
        if r.health_flag:
            print(f"  Health   : {r.health_flag}")
        for w in r.warnings:
            print(f"  ! {w}")


def _print_json(results: list[ResolvedAnimal]) -> None:
    print(json.dumps([dataclasses.asdict(r) for r in results], indent=2))


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agriwebb-waddl", description="Resolve vial tokens to WADDL submission fields."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_resolve = sub.add_parser("resolve", help="Resolve last-4 EID / VID / name tokens to WADDL fields.")
    p_resolve.add_argument("tokens", nargs="+", help="Vial identifiers (last-4 EID, VID, or name).")
    p_resolve.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    p_resolve.add_argument("--as-of", metavar="YYYY-MM-DD", help="Compute ages relative to this date (default: today).")

    args = parser.parse_args(argv)

    if args.command == "resolve":
        try:
            results = resolve(args.tokens, today=_parse_as_of(args.as_of))
        except FileNotFoundError as e:
            raise SystemExit(f"error: {e}") from None
        if args.json:
            _print_json(results)
        else:
            _print_table(results)
        missing = [r.token for r in results if not r.found]
        if missing:
            print(f"\n{len(missing)} unresolved: {', '.join(missing)}", file=sys.stderr)
            return 1
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(cli())
