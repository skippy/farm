# WADDL Submission Autofill

How to fill a WADDL (Washington Animal Disease Diagnostic Lab) CoreOne blood-draw
submission from a list of vial tags, using the AgriWebb cache for the per-animal
data. This turns a 20-30 minute manual job into a short one.

Portal: <https://waddl.labs.tracefirst.com> (CoreOne for Labs, **production**).
Two pieces:

- **`agriwebb-waddl` resolver** (`agriwebb.waddl.resolver`) — pure data: maps each
  vial token to the animal's WADDL fields from `animals.json`. No browser.
- **This playbook** — the browser-driving recipe (Playwright), executed by Claude.

> **Safety:** this is the production portal with a real balance. Fill the draft
> only. **Never** click *Complete Submission*, touch payment, or delete via a
> confirm dialog without the user asking. Leave a filled, unsubmitted draft.

---

## The workflow

### 0. Ask the two variable questions FIRST
Everything else is constant. Ask up front, before touching the form, because
values **must be baked in at add-time** — editing rows afterward does not persist
(see Gotchas):

1. **Collection date(s)** — usually one draw date for the whole batch, but
   batches can differ. Ask every time.
2. **Which test(s)** — default **CL** (*Corynebacterium pseudotuberculosis /
   Caseous lymphadenitis*); sometimes **CL + Johne's**; sometimes the
   **Small Ruminant Biosecurity Serologic Panel**.

### 1. Resolve the tag list
```bash
agriwebb-waddl resolve 0693 7854 ww36 Camas --json      # add --as-of YYYY-MM-DD to fix "today"
```
Each result carries: `id_field` (type verbatim), `taxon` + `breed_id`,
`sex_code` + `sex_code_id`, `age` + `age_unit` + `age_uom_id`, `health_flag`,
and `warnings`. **Surface every `warnings` entry to the user before filling** —
`resolver.py` is the authoritative list, but they cover: not-found (refresh cache
via `agriwebb-livestock cache --refresh`, then retry, or ask for the full VID/name);
ambiguous match (a tag matched >1 animal — the on-farm one was chosen, confirm it);
incomplete pedigree / no pedigree (breed only partially known — confirm); near-tie
crosses (broke to the dam's breed, or to the named/Finn fallback — confirm); and
age approximated or unknown (set manually).

### 2. Open the form
Log in (user does this — never enter credentials). Dashboard → **Full Form**.
The wizard has 10 steps: Accession, Veterinarian/Owner, Additional Data, Setup,
Animals, Specimen, Tests, Review, Agreement, Print. Navigate with the **Next /
Next Step** buttons — the numbered step badges are not scriptable; after a reload
(which lands on step 1) click Next to walk back.

### 3-4. Accession / Vet / Additional Data / Setup (all constant)
| Field | Value |
|---|---|
| Receiving Lab | **WADDL - Pullman (W)** |
| Carrier | **UPS** |
| Gross Necropsy / Export Case | **N/A** |
| Veterinarian | **Adam Greene** (confirm) |
| Owner | **Current Owner** tab (auto-fills Adam Greene, Friday Harbor) |
| Med Records # / Med History / add-on services | leave blank / unchecked |
| Setup | Animal (not Non-Animal); bulk-upload unchecked |

### 5. Animals — one row per tag
Fill the **Add Animal** template row, click **Add Animal**, repeat. Fill all four
fields explicitly (don't trust residual values). See the fill recipe below.

### 6. Specimen — one specimen per animal
Standing defaults: **Clotted Blood (CB)** / **Acute** / **Red Top Tube (RTT)**.
Set the animal, the three defaults, and the **collection date** in the template,
then **Add Specimen**. The portal auto-creates one blank specimen for the first
animal — delete + re-add it via the template so its date persists (or have the
user set that one date).

### 7. Tests — the ask-every-time selection
Search & pick the test, check **All Specimens** (applies to all at once — no
loop), click **Add Test**. Capture the exact catalog name/id per test the first
time each is used (CL / Johne's / Biosecurity).

### 8-10. Review / Agreement / Print
Stop here. Hand the filled draft to the user to review and **Complete Submission**.

---

## Browser fill recipe (Playwright + injected JS)

The form uses **bootstrap-select** (jQuery present). Set native `<select>.value`,
then `$(sel).selectpicker('val', v)`, then dispatch `change`. Inputs: set value +
dispatch `input`/`change`. Date fields are **flatpickr**: `input._flatpickr.setDate('YYYY-MM-DD', true)`.

### Animals step selectors
| Field | Selector | Value |
|---|---|---|
| Tag | `#animal_template_incremental_tag_id` | `id_field` |
| Taxon | `select[name="animal_template[breed_id]"]` | `breed_id` |
| Sex | `select[name="animal_template[sex_code_id]"]` | `sex_code_id` |
| Age | `#animal_template_age` | `age` |
| Age unit | `select[name="animal_template[age_uom_id]"]` | `age_uom_id` |
| Add | `#add-record-template-btn` | click |
| Committed tags (dedup) | `input[name="animal_tags[num]"]` | read `.value` |

**Per animal:** skip if tag already in committed set → set tag → **ensureTaxon**
→ set breed/sex/age/unit → click Add → **poll until the "Delete Animal" count
increments** before the next.

`ensureTaxon(breed_id)`: the Taxon select is AJAX and resets to 1 option on
navigation, so if the option is missing, append
`<option value=breed_id>{full taxon string}</option>` then `selectpicker('refresh')`
before setting.

### Specimen step selectors
`specimen_template[animal_id]` (value = animal internal id — from the picker),
`specimen_template[specimen_type_id]`, `...[specimen_type_description_id]`,
`...[transport_medium_id]`, `input[name="specimen_template[collection_date]"]`
(flatpickr), Add = `#add-record-template-btn`, count via "Delete Specimen".

### Portal internal IDs (Oak Knoll production; re-derive if the portal renumbers)
- **Breed / Taxon:** Finnish-Landrace `3569`, North Country Cheviot `3591`,
  Bluefaced Leicester `3547` (prefix `mammalia :: bovidae :: ovis :: aries :: Domestic Sheep :: `).
  The resolver emits `breed_id` directly.
- **Sex code:** Female (F) `2`, Male (M) `3`, Male Neutered/wether (C) `5`, Unknown (U) `1`.
- **Age unit:** years `21`, months `20`, weeks `43`, days `22`.
- **Specimen:** Clotted Blood (CB) `266`, Acute `1097`, Red Top Tube (RTT) `2`
  (single-sourced as `SPECIMEN_IDS` in `resolver.py`; not resolver-derived, so
  re-verify against the portal if it renumbers).

To (re)discover a breed_id: open the Taxon dropdown, set its search input to the
breed and fire `input`+`keyup`, wait ~1.5s, then read the matching option's value.

---

## Gotchas (learned the hard way)

- **Ask date & tests first, bake in at add-time.** Editing a committed row's
  fields relies on a **debounced autosave** that silently drops rapid or repeated
  edits — dates especially never persisted on edit. Values entered in the Add
  template *do* persist (they're in the creation POST).
- **Verify by reload.** Autosave "success" is not observable in the DOM. After a
  batch, reload and re-read to confirm. Reload lands on step 1 → Next back.
- **Taxon options reset** to 1 on navigation → always `ensureTaxon` before setting a breed.
- **Cross breed = dominant pedigree fraction**; a near-tie (top two within
  `TIE_THRESHOLD`, ~15 points — not literally 50/50) breaks to the dam's breed,
  then Finn if it's a candidate, then the highest-ranked family. A `"<Breed> X"`
  label is a first-cross, not a purebred, and an incomplete pedigree (a parent
  off-cache) is flagged rather than reported at false 100% confidence.
- **Cache freshness:** a token miss usually means a newer animal —
  `agriwebb-livestock cache --refresh` and retry.
- **Dedup** against `input[name="animal_tags[num]"]`, not the template input.
- The bulk "Update Specimens Below" / delete buttons need a **trusted** click and
  may raise a confirm dialog — prefer add-time correctness over post-hoc editing.
