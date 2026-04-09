# Farm Project - Claude Context

This workspace contains tools for farm management, primarily integrating with AgriWebb.

## Project Structure

```
farm/
├── .cache/                        # Local data cache (gitignored)
│   ├── animals.json               # All animals with records
│   ├── natural_service.json       # Breeding groups (scraped from portal)
│   ├── lamb_losses_YYYY.json      # Categorized losses per year
│   ├── paddock_soils.json         # USDA soil data per paddock
│   ├── weather_historical.json    # Open-Meteo weather (2018+)
│   ├── noaa_weather.json          # NOAA station data
│   ├── ndvi_historical.json       # Monthly NDVI per paddock (2018+)
│   └── growth_estimates.json      # Latest growth calculations
├── .claude/                       # Claude Code settings
├── .github/workflows/             # GitHub Actions (daily/weekly sync)
├── agriwebb/                      # AgriWebb integration package
│   ├── src/agriwebb/
│   │   ├── analysis/lambing/      # Lambing analysis (loader, reports)
│   │   ├── core/                  # Shared utilities (cache, config, timestamps)
│   │   ├── data/                  # Livestock, soils, grazing data
│   │   ├── pasture/               # Growth models, biomass, SDM
│   │   ├── satellite/             # GEE NDVI, NLCD, moss detection
│   │   ├── sync/                  # Push data to AgriWebb
│   │   └── weather/               # NOAA, Open-Meteo, rainfall
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

The `.cache/animals.json` file contains all animals with full details. Always prefer reading this file over making API calls — it's faster and doesn't hit rate limits.

For lambing analysis, use the loader:
```python
from agriwebb.analysis.lambing.loader import load_farm_data
data = load_farm_data(season=2026)
# data.animals, data.by_id, data.service_groups, data.loss_records
```

For raw access:
```python
from agriwebb.core.cache import load_cache_json
animals = load_cache_json("animals.json", key="animals")
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

Use the lambing analysis loader for all livestock analysis:
```python
from agriwebb.analysis.lambing.loader import load_farm_data, get_name, get_breed, get_ancestors
data = load_farm_data(season=2026)
```

The loader provides typed helpers for animal classification, lineage, loss analysis,
and breeding group membership. See `agriwebb/src/agriwebb/analysis/lambing/loader.py`.

---

## Lambing Analysis Conventions

**These conventions are non-negotiable when analyzing lambing data for this farm.**

### Counting lambs
- **"Born" and sex counts = live lambs only** (`fate == 'Alive'`)
- **`fate=Sold` = successfully raised/harvested** — this is a SUCCESS, never count as a loss
- **`fate=Dead` = actual loss** — but must be further classified (see below)
- Lambing rate = live lambs / ewes (per-lambed or per-joined — always label which)
- Always show losses as a SEPARATE metric from lambing rate, never blended

### Loss classification
Deaths are NOT all "stillborn." Classify by mechanism:

| Category | daysReared | Description |
|----------|-----------|-------------|
| **Prenatal** | N/A (found at delivery) | Died in utero before labor — mummified, underdeveloped, entangled |
| **Intrapartum** | 0 or None | Died during delivery — breech, dystocia, cord complications |
| **Perinatal** | 0 or None | Born alive, died within minutes/hours — asphyxia, hypothermia |
| **Early loss** | 1–90 days | Neonatal/young lamb death — disease, FTT, enterotoxemia |
| **Late death** | >90 days | Not lambing-related — accident, disease in older lambs |

Use `#loss-*` hashtag tags in AgriWebb notes to categorize:
`#loss-mummified`, `#loss-entangled`, `#loss-underdeveloped`, `#loss-dystocia`,
`#loss-asphyxia`, `#loss-mismothering`, `#loss-hypothermia`, `#loss-weak`

Detailed loss records are cached in `.cache/lamb_losses_YYYY.json`.

### Language
- Use respectful language about all animals — no dismissive framing
- Say "loss" not "death" in reports; "no longer on farm" not "culled"
- Present data clinically without value judgments on individual animals

### Key findings (2026 season)
- **Highest risk profile:** first-time mother, age 4+, carrying triplets+ = 28% loss rate
- **Safest profile:** experienced ewe carrying twins = 0% loss (72 lambs, zero losses ever)
- **First-time mothers lose 4.4× more lambs than experienced** (14.1% vs 3.2%)
- **Breed ewes young:** first lambing at age 2–3 = 12% loss; age 4+ = 19% loss
- **NCC×NCC is highest-risk breed cross** (15.4% loss vs 6.2% for NCC×other)
- **Tyne (NCC ram) has 27% loss rate** on NCC dams — all 3 losses were female lambs
- **Solar (Finnsheep ram) is the gold standard:** 59 lambs, 1.7% loss over 5 years
- **Hope (NCC ram) is the best NCC sire:** 18 lambs, 0% loss, but 1 assisted birth (Andie, oxytocin)

### NCC gene pool
The NCC foundation is narrow — most ewes trace back to Perseus × Persephone.
Three NCC AI donors are available: **Armadale-Winston** (sired Hope, cleanest line),
**Achentoul-10097** (sired Tyne — mixed signal), **Calla-Andrew** (sired Quid).
See `.cache/natural_service.json` for current breeding group data.

### CDT vaccination timing
Ewe pre-lambing CDT booster must be given **4 weeks before expected lambing start**
(not 3–8 days, as was done in 2026). Lamb first CDT at marking (~4–6 weeks).

### AgriWebb API gaps
The public GraphQL API does NOT expose: Natural Service, Birth, Death, Lambing,
Castrate, Wean, Sale, Tag, or Movement records. These are only in the portal UI.
A Playwright MCP browser integration is configured for portal scraping when needed.
Session data persists in `~/Library/Caches/ms-playwright/mcp-chrome-profile`.
