# Lambing Analysis — Conventions, Methodology & Findings

This document captures the analytical framework and key patterns for lambing
analysis on this farm. It's meant to be read by an agent working on livestock
questions — not loaded by default, but referenced from CLAUDE.md.

Use `agriwebb.analysis.lambing.loader` for all data access. Use the MCP tools
(`mcp__agriwebb__get_*`) for interactive exploration.

---

## Counting Conventions

**These are non-negotiable.**

- **"Born" and sex counts = live lambs only** (`fate == 'Alive'`)
- **`fate=Sold` = successfully raised/harvested** — this is a SUCCESS, never a loss
- **`fate=Dead` = actual loss** — must be further classified (see below)
- **Lambing rate** = live lambs / ewes — always label whether per-lambed or per-joined
- **Losses are a SEPARATE metric** from lambing rate — never blended into the headline number
- When comparing breeds or sires, **live-lamb-per-dam** is the primary performance metric

## Loss Classification

Deaths are NOT all "stillborn." Classify by mechanism:

| Category | daysReared | Description |
|----------|-----------|-------------|
| **Prenatal** | N/A (found at delivery) | Died in utero before labor — mummified, underdeveloped, entangled |
| **Intrapartum** | 0 or None | Died during delivery — breech, dystocia, cord complications |
| **Perinatal** | 0 or None | Born alive, died within minutes/hours — asphyxia, hypothermia |
| **Early loss** | 1–90 days | Neonatal/young lamb death — disease, FTT, enterotoxemia |
| **Late death** | >90 days | Not lambing-related — accident, disease in older lambs |

### Hashtag convention for AgriWebb notes
Tag each loss with exactly one `#loss-*` tag plus freeform detail:
`#loss-mummified`, `#loss-entangled`, `#loss-underdeveloped`, `#loss-dystocia`,
`#loss-asphyxia`, `#loss-mismothering`, `#loss-hypothermia`, `#loss-weak`

Example: `#loss-dystocia breech presentation; large ram lamb, one leg back`

Detailed loss records are cached in `.cache/lamb_losses_YYYY.json`.

## Language

- Use respectful language about all animals — no dismissive framing
- Say "loss" not "death"; "no longer on farm" not "culled"
- Present data clinically without value judgments on individual animals
- Never say "good riddance" or similar about any animal

---

## Risk Factor Analysis

### The risk ladder — factors ranked by impact

| Factor | Low-risk | Loss rate | High-risk | Loss rate | Spread |
|--------|----------|-----------|-----------|-----------|--------|
| **Experience** | Experienced | ~3% | First-time | ~14% | ~11pp |
| **Litter size** | Twins | ~4% | Triplets+ | ~16% | ~12pp |
| **Breed cross** | NCC × other | ~6% | NCC × NCC | ~15% | ~9pp |
| **Age at first lambing** | Age 2–3 | ~8% | Age 4+ | ~15% | ~7pp |

### Compounding risk profiles

```
All lambing events                               ~9% loss (baseline)
│
├── Experienced mother                           ~3%
│   ├── + twins                                  ~0% ← safest profile
│   └── + triplets+                              ~8%
│
└── First-time mother                            ~14%
    ├── First lambing at age 2-3                 ~12%
    └── First lambing at age 4+                  ~19%
        └── + Triplet+ litter                    ~28% ← highest risk
```

### Key analytical principles

1. **Experience is the single strongest predictor.** First-time mothers lose ~4× more
   lambs than experienced ewes. This dwarfs breed, sire, and litter-size effects.

2. **Breed ewes young.** First lambing at age 2–3 produces ~12% loss; waiting until
   age 4+ jumps to ~19%. The pelvic conditioning from a first lambing at age 2
   benefits all subsequent pregnancies.

3. **Twins are the safest litter size.** Experienced ewes carrying twins have
   essentially zero historical losses. Singles (especially NCC×NCC) carry
   dystocia risk from large lamb size. Triplets+ carry intrauterine crowding risk.

4. **NCC×NCC crosses carry elevated risk.** NCC lambs are larger-framed; when both
   parents are NCC, the lamb is at maximum frame size for the breed. Crossing NCC
   ewes to Finnsheep or 1st Cross sires roughly halves the loss rate.

5. **Quad litters from Finnsheep almost always lose at least one lamb.** This is
   intrauterine crowding, not a management failure. The prenatal losses
   (mummified/underdeveloped fetuses) are not preventable at lambing time —
   the intervention point is earlier (nutrition, ultrasound, managing quad ewes).

6. **Don't call ewes "empty" during the lambing window.** Compute the actual
   per-group window from joining dates + 147 days. A ewe that conceived on her
   second estrous cycle (~day 17 of joining) will lamb near the END of the window.
   Defer to the shepherd's field observation over statistical guesses.

---

## Sire Analysis Methodology

### What to compute per sire
- Total lambs across all years
- Raised (alive + sold) vs lost (stillborn + early loss)
- Loss rate = lost / total
- Breakdown by dam breed (NCC×NCC vs NCC×other vs cross)
- Generational patterns: check the sire's offspring AND their offspring

### Grandsire tracing
When a sire has elevated loss rates, check whether his DAUGHTERS also have
elevated losses when they lamb. This identifies heritable dystocia/loss patterns
that persist across generations.

### NCC sire evaluation
NCC sires should always be evaluated with dam-breed breakdown because
NCC×NCC carries structurally higher risk than NCC×cross. A sire with 25%
loss rate on NCC dams but 0% on cross dams isn't necessarily a bad sire —
the breed combination is the risk factor.

### Assisted births
Treatment records near birth dates can reveal difficult deliveries where the
lamb survived. Look for: oxytocin (labor induction), flunixin (pain management),
penicillin (infection). These don't show up as losses but indicate dystocia risk.

---

## NCC Genetic Diversity

### The bottleneck
The NCC ewe flock traces back to a small number of foundation animals. Most
NCC ewes share common ancestors 2–3 generations back. This means:
- Most NCC rams on-farm are related to most NCC ewes
- AI with outside NCC sires is the primary tool for introducing new genetics
- When evaluating NCC ram × ewe pairings, always check shared ancestors

### AI donors
Three NCC rams are available as AI donors. Their sons on-farm provide comparison
data for evaluating each line's lambing success. Check the reference memory
file for current donor details.

### Compatibility analysis
Use `get_ancestors()` on both ram and ewe, then compare ancestor sets. Shared
ancestors within 4 generations (~6.25% inbreeding coefficient) should be flagged.
The `get_ncc_compatibility` MCP tool does this automatically.

---

## CDT Vaccination

### The gap problem
NCC lambs are aggressive eaters and prone to enterotoxemia (Clostridium
perfringens type D / "overeating disease"). The standard CDT vaccination
chain has a potential gap:

```
Ewe CDT booster → 4 weeks → colostrum with antibodies → lamb protected 6-8 weeks
                                                          ↓
Lamb first CDT at marking (~4-6 weeks) → booster 3-4 weeks later → own immunity
```

If the ewe booster is given too late (days before lambing instead of 4 weeks),
colostral antibody levels are suboptimal. This creates a window where lambs
have no CDT protection.

### Protocol
- **Ewe booster:** 4 weeks before expected lambing start (not 1 week)
- **Lamb first CDT:** at marking (~4–6 weeks)
- **Lamb booster:** 3–4 weeks after first dose
- **High-risk lambs (aggressive NCC eaters):** consider C&D antitoxin at birth
  for immediate protection until vaccine immunity develops

---

## AgriWebb API Gaps

The public GraphQL API (`api.agriwebb.com/v2`) does NOT expose:
- Natural Service, Birth, Death, Lambing, Castrate, Wean, Sale, Tag, or Movement records
- These exist as Session containers (visible via `sessions` query) but their
  records return `[]` through both `session.records` and `records(sessionId: ...)`
- Only 6 record types are queryable: feed, weigh, score, animalTreatment,
  locationChanged, pregnancyScan

### Portal scraping
A Playwright MCP browser integration is configured for portal scraping when the
API falls short. Session data persists in the Playwright profile directory.
Natural service data is cached in `.cache/natural_service.json` after scraping.

### Internal API discovery
The portal uses an internal event-sourcing API at `loopback-cdn.agriwebb.io`
with endpoints like `EventSourcingService/search`, `/query`, `/aggregate`.
This is NOT accessible with the public API key — it uses a separate session-based
auth from the portal login flow. Future work may explore using these endpoints
via the Playwright session's auth context.

---

## Standard Analysis Workflow

When asked about lambing, the typical investigation path is:

1. **Season overview:** `get_lambing_season()` — headline numbers, live lambs, rates
2. **Loss breakdown:** `get_losses()` — categorized by mechanism, by sire, by breed
3. **Sire deep dive:** `get_sire_stats(sire)` — if a specific sire is flagged
4. **Lineage check:** `get_ancestors()` + `get_offspring()` — for pattern tracing
5. **Breeding compatibility:** `get_ncc_compatibility()` — for fall joining planning
6. **Experience analysis:** compute first-time vs experienced from `get_lambing_season()`

Always start with live counts and work toward losses, not the other way around.
The loss analysis is a subset investigation, not the headline.
