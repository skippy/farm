# Breed purity (lineage-derived)

## Problem

AgriWebb's per-animal **Breed** is a pick from a fixed, global list managed by
AgriWebb (Account → Ranch Management → Settings → Favourite Breeds). There is no
way to add a custom breed and the list has no "Finnish Landrace X" (the only Finn
entry is AgriWebb's misspelled "Finish Landrace"). On this farm the label is
therefore used loosely: predominantly-Finn crosses are also labelled
"Finish Landrace". **The label cannot answer "is this animal purebred?".**

## Approach

`agriwebb.analysis.lineage.purity` derives breed composition from recorded
parentage:

1. Walk sires/dams recursively until reaching a **founder**:
   - a cached animal with no recorded parents, or
   - an off-cache parent that exists only as a name/VID in
     `parentAnimalIdentity` (`parentAnimalId` is `null` for these — spellings
     vary: `BLUEMAN` / `Blueman` / `Blue Man`).
2. Founders' breed status is asserted by the shepherd in
   **`src/agriwebb/analysis/lineage/founders.json`** (checked in; edit by hand).
3. Composition = average of the two parents' compositions. Unclassified founders
   and missing parents contribute `"unknown"`.

### Verdicts

| `purebred` | `status`     | Meaning |
|-----------|--------------|---------|
| `true`    | `verified`   | every founder resolved and purebred of one breed |
| `false`   | `mixed`      | a founder is a known cross, or two breeds meet anywhere in the tree (conclusive even if other founders are unclassified) |
| `null`    | `unverified` | unclassified founders and/or parentage gaps — see `unresolved` / `gaps` |

### founders.json entry

Paths below are relative to the `agriwebb/` package directory; the file is
`src/agriwebb/analysis/lineage/founders.json` (next to `purity.py`).

```json
{
  "id": "BLUEMAN",
  "animalId": null,
  "aliases": ["Blue Man"],
  "breed": "Bluefaced Leicester X",
  "purebred": false,
  "composition": null,
  "notes": "Shepherd verdict 2026-08-20"
}
```

| Field | Meaning |
|---|---|
| `id` | human label; primary match key for external founders |
| `animalId` | set for cached animals — pins the verdict and overrides recorded parents |
| `aliases` | alternate spellings / VIDs; matching is case- and punctuation-insensitive |
| `breed` | canonical breed (required when `purebred` is `true`) |
| `purebred` | `true` / `false` / `null` (unclassified) |
| `composition` | optional for `purebred: false`, e.g. `{"Finnish Landrace": 0.5, "Bluefaced Leicester": 0.5}`; fractions are normalised |
| `notes` | provenance of the verdict |

Loading fails loudly (`RegistryError`) on invalid JSON, a missing `id`,
`purebred: true` without `breed`, or two entries claiming the same alias /
`animalId`. More than one recorded sire (or dam) is averaged as a best estimate
and recorded as a gap, so such an animal can never be `verified`.

External parents are matched by normalised **name first, then VID** against
`id` + `aliases`. Breed names are canonicalised (`Finish Landrace`, `Finn`,
`Finnsheep` → `Finnish Landrace`; `NCC`; `BFL`).

## Usage

```bash
agriwebb-lineage founders                 # founders still needing a verdict, most descendants first
agriwebb-lineage founders --all --json
agriwebb-lineage purity Bocce             # composition + verdict for one animal
agriwebb-lineage purebred Finn --year 2025          # purebred / unverified / mixed (on-farm)
agriwebb-lineage purebred "North Country Cheviot" --all
```

MCP tools: `get_breed_purity(animal)`, `get_purebred_animals(breed, year?, on_farm_only=True)`,
`get_unclassified_founders()`.

## Workflow for classifying founders

1. Run `agriwebb-lineage founders` (or the MCP tool). Entries are pre-populated
   with spelling aliases and hints (AgriWebb label, birth year, AI-donor notes).
   Registry VIDs that embed breeder names are deliberately **not** stored in
   this public repo — keep them in AgriWebb.
2. For each founder set `breed` and `purebred` (and optionally `composition`).
   Merge duplicates by moving one entry's `id` into the other's `aliases`.
3. Re-run `agriwebb-lineage purebred <breed>`; `unverified` shrinks as verdicts
   land. New founders appearing after an `agriwebb-sync` show up as
   `NOT IN FILE` in the founders listing — add them.

When a founder's verdict is wrong, everything downstream is wrong: keep `notes`
on where the assertion came from (registration papers, breeder, memory).
