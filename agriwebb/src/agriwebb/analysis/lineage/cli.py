"""CLI for lineage-derived breed purity.

Usage::

    agriwebb-lineage founders                    # founders still needing a verdict (most descendants first)
    agriwebb-lineage founders --all              # include classified founders
    agriwebb-lineage founders --json
    agriwebb-lineage purity Bocce                # one animal's composition + verdict
    agriwebb-lineage purebred "Finnish Landrace" --year 2025     # purebred / unverified / mixed lists
    agriwebb-lineage purebred Finn --year 2025 --all             # include off-farm animals

Founder verdicts live in ``founders.json`` next to ``purity.py`` (path relative to
the ``agriwebb/`` package: ``src/agriwebb/analysis/lineage/founders.json``). Edit by
hand; see ``agriwebb/docs/breed-purity.md``.
"""

from __future__ import annotations

import argparse
import json

from agriwebb.analysis.lambing.loader import find_animal, load_farm_data
from agriwebb.analysis.lineage.purity import (
    DEFAULT_REGISTRY_PATH,
    FounderRegistry,
    compute_purity,
    discover_founders,
    find_purebred,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_comp(comp: dict[str, float]) -> str:
    return ", ".join(f"{k} {v:.1%}" for k, v in comp.items())


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_founders(args: argparse.Namespace) -> int:
    data = load_farm_data()
    reg = FounderRegistry.load()
    cands = discover_founders(data.animals, data.by_id, reg)
    if not args.all:
        cands = [c for c in cands if not c.resolved]
    if args.json:
        print(json.dumps([c.to_dict() for c in cands], indent=2))
        return 0
    print(f"\nFounders ({'all' if args.all else 'unclassified'}): {len(cands)}   registry: {DEFAULT_REGISTRY_PATH}\n")
    print(f"{'founder':20} {'role':5} {'desc':>5} {'status':12} hints")
    print("-" * 80)
    for c in cands:
        label = c.registry_id or (sorted(c.labels)[0] if c.labels else c.key)
        status = "classified" if c.resolved else ("in registry" if c.registry_id else "NOT IN FILE")
        hints = []
        if c.breed_hint:
            hints.append(f"label={c.breed_hint}")
        if c.birth_year:
            hints.append(f"born {c.birth_year}")
        other = sorted((c.labels | c.vids) - {label})
        if other:
            hints.append("aka " + " / ".join(other))
        print(f"{label:20} {c.role:5} {c.descendants:>5} {status:12} {'; '.join(hints)}")
    return 0


def cmd_purity(args: argparse.Namespace) -> int:
    data = load_farm_data()
    reg = FounderRegistry.load()
    animal = find_animal(args.animal, data.animals, data.by_id)
    if animal is None:
        print(f"No animal matching '{args.animal}'")
        return 1
    r = compute_purity(animal["animalId"], data.by_id, reg)
    if args.json:
        print(json.dumps(r.to_dict(), indent=2))
        return 0
    verdict = {True: f"PUREBRED {r.purebred_breed}", False: "MIXED", None: "UNVERIFIED"}[r.purebred]
    print(f"\n{r.name}  (label: {r.breed_label})  ->  {verdict}")
    print(f"  composition: {_fmt_comp(r.composition)}")
    if r.founders:
        print(f"  founders:    {', '.join(r.founders)}")
    if r.unresolved:
        print(f"  unresolved:  {', '.join(r.unresolved)}")
    for g in r.gaps:
        print(f"  gap:         {g}")
    return 0


def cmd_purebred(args: argparse.Namespace) -> int:
    data = load_farm_data()
    reg = FounderRegistry.load()
    res = find_purebred(data.animals, data.by_id, reg, breed=args.breed, year=args.year, on_farm_only=not args.all)
    if args.json:
        print(json.dumps({k: [r.to_dict() for r in v] for k, v in res.items()}, indent=2))
        return 0
    scope = f"born {args.year}" if args.year else "all years"
    scope += ", on-farm" if not args.all else ", incl. off-farm"
    for status, title in (("purebred", "PUREBRED"), ("unverified", "UNVERIFIED"), ("mixed", "MIXED")):
        rows = res[status]
        print(f"\n{title} ({len(rows)}) - {scope}")
        for r in rows:
            extra = ""
            if status == "unverified":
                extra = "  needs: " + ", ".join(r.unresolved + r.gaps)
            elif status == "mixed":
                extra = "  " + _fmt_comp(r.composition)
            print(f"  {r.name:16} label={r.breed_label:24}{extra}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agriwebb-lineage", description="Lineage-derived breed purity")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("founders", help="List founder animals needing a breed verdict")
    f.add_argument("--all", action="store_true", help="Include already-classified founders")
    f.add_argument("--json", action="store_true")
    f.set_defaults(func=cmd_founders)

    u = sub.add_parser("purity", help="Breed composition + verdict for one animal")
    u.add_argument("animal", help="name / VID / EID / EID suffix / animalId")
    u.add_argument("--json", action="store_true")
    u.set_defaults(func=cmd_purity)

    b = sub.add_parser("purebred", help="List purebred / unverified / mixed animals for a breed")
    b.add_argument("breed", help="e.g. 'Finnish Landrace', 'Finn', 'North Country Cheviot'")
    b.add_argument("--year", type=int, help="birth year")
    b.add_argument("--all", action="store_true", help="Include off-farm animals")
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_purebred)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli())
