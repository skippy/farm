# Farm Project - Claude Context

This workspace contains tools for farm management, primarily integrating with AgriWebb.

## Project Structure

```
farm/
├── .cache/                        # Local data cache (gitignored)
│   ├── animals.json               # All animals with records
│   ├── paddock_soils.json         # USDA soil data per paddock
│   ├── weather_historical.json    # Open-Meteo weather (2018+)
│   ├── noaa_weather.json          # NOAA station data
│   ├── ndvi_historical.json       # Monthly NDVI per paddock (2018+)
│   └── growth_estimates.json      # Latest growth calculations
├── .claude/                       # Claude Code settings
├── .github/workflows/             # GitHub Actions (daily/weekly sync)
├── agriwebb/                      # AgriWebb integration package
│   ├── src/agriwebb/
│   └── tests/
└── CLAUDE.md                      # This file
```

## Key Commands

### Animals
```bash
agriwebb-sync                              # Download all animals to animals.json
agriwebb-livestock cache                   # Same as agriwebb-sync
agriwebb-livestock get X                   # Look up animal by name/ID/EID/VID
agriwebb-livestock lineage X --generations 3
agriwebb-livestock offspring X
agriwebb-livestock summary                 # Herd summary by species/breed/status
```

### Weather / Rainfall
```bash
agriwebb-weather sync                  # Sync rainfall (NOAA + Open-Meteo for real-time)
agriwebb-weather sync --days 14        # Sync last 14 days
agriwebb-weather backfill --months 1   # Backfill last month (NOAA only)
agriwebb-weather backfill --years 2    # Backfill 2 years
agriwebb-weather list                  # List existing rainfall records
```

Rainfall uses two sources:
- **NOAA/NCEI**: Station data, accurate but 5-6 day delay
- **Open-Meteo**: Model data, near-real-time, fills gaps until NOAA available

### Pasture Growth
```bash
agriwebb-pasture estimate                   # Weather-driven growth estimates
agriwebb-pasture estimate --forecast        # Include 7-day projection
agriwebb-pasture sync --growth-rate         # Push growth rates to AgriWebb
agriwebb-pasture sync --sdm                 # Push standing dry matter (satellite NDVI)
agriwebb-pasture sync --sdm --dry-run       # Preview without pushing
```

Growth model uses:
- Weather data (temperature, precipitation, ET₀)
- Soil properties (drainage, AWC from USDA)
- Seasonal calibration for PNW cool-season grasses

SDM calculation uses:
- NDVI from satellite (Harmonized Landsat Sentinel-2)
- Seasonal calibration model (winter/spring/summer/fall)
- Tree masking via NLCD

### Cache Commands
```bash
agriwebb-livestock cache                    # Download all animals
agriwebb-livestock cache --refresh          # Force full re-download
agriwebb-weather cache                      # Download weather data
agriwebb-weather cache --refresh            # Force full re-fetch
agriwebb-pasture cache                      # Download weather, soil, NDVI history
agriwebb-pasture cache --refresh            # Force full re-fetch
```

### GitHub Actions (Automated Sync)
- **Daily** (6 AM UTC): Weather sync + growth rate estimates
- **Weekly** (Sunday 8 AM UTC): Satellite SDM + rainfall

Run locally to test:
```bash
agriwebb-weather sync --days 7 --dry-run           # Daily weather
agriwebb-pasture sync --growth-rate --dry-run      # Daily growth
agriwebb-pasture sync --sdm --dry-run              # Weekly SDM
```

## Local Data Analysis

The `.cache/animals.json` file contains all animals with full details. Always prefer reading this file over making API calls - it's faster and doesn't hit rate limits.

```python
import json
from pathlib import Path

cache_file = Path('/Users/greene/Documents/dev/farm/.cache/animals.json')
with open(cache_file) as f:
    data = json.load(f)

animals = data['animals']
by_id = {a['animalId']: a for a in animals}
```

---

## Sheep Terminology Reference

### By Sex & Age
| Term | Description |
|------|-------------|
| **Ram** | Intact adult male (can breed) |
| **Wether** | Castrated male (cannot breed) |
| **Ewe** | Adult female |
| **Maiden ewe** | Female that hasn't lambed yet |
| **Lamb** | Young sheep (under 1 year) |
| **Hogget/Yearling** | 1-2 year old sheep |

### AgriWebb Age Classes
| ageClass | Meaning |
|----------|---------|
| `ram` | Intact breeding male |
| `ram_lamb` / `ram_weaner` / `ram_hogget` | Young intact male |
| `wether` / `wether_weaner` / `wether_hogget` | Castrated male |
| `ewe` | Adult female |
| `maiden_ewe` | Female, hasn't lambed |
| `ewe_lamb` / `ewe_weaner` / `ewe_hogget` | Young female |

### Breeding Terms
| Term | Description |
|------|-------------|
| **Tupping/Joining** | Mating season |
| **Gestation** | ~147 days (5 months) |
| **Dry** | Not pregnant/lactating |
| **Empty** | Not pregnant after breeding |

### Inbreeding Coefficients
| Relationship | Coefficient | Risk |
|--------------|-------------|------|
| Parent-offspring | 25% | Avoid |
| Full siblings | 25% | Avoid |
| Half siblings | 12.5% | Moderate |
| First cousins | 6.25% | Low-moderate |
| Half first cousins | 3.125% | Low |

### Breeds on This Farm
- **Finnsheep (Finnish Landrace)** - Prolific, good mothers, maternal breed
- **North Country Cheviot** - Hardy, good meat, dual purpose
- **Bluefaced Leicester** - Crossing sire, lustrous wool
- **1st Cross** - First generation crossbreed

## Common Analysis Patterns

### Find intact breeding rams (not wethers)
```python
rams = [a for a in animals
        if (a.get('characteristics') or {}).get('sex') == 'Male'
        and 'ram' in ((a.get('characteristics') or {}).get('ageClass') or '').lower()
        and 'wether' not in ((a.get('characteristics') or {}).get('ageClass') or '').lower()
        and (a.get('state') or {}).get('onFarm')]
```

### Get ancestors for inbreeding check
```python
def get_ancestors(animal, by_id, depth=0, max_depth=6):
    if not animal or depth > max_depth:
        return set()
    ancestors = set()
    for parent_type in ['sires', 'dams']:
        for p in ((animal.get('parentage') or {}).get(parent_type) or []):
            pid = p.get('parentAnimalId')
            if pid:
                ancestors.add(pid)
                if pid in by_id:
                    ancestors.update(get_ancestors(by_id[pid], by_id, depth + 1, max_depth))
    return ancestors
```
