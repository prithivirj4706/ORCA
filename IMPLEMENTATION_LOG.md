# ORCA — Implementation Log

**Session:** 2026-09-02 · **Phase:** design set → Phase 1–6 partial
**State at end of session:** ~70% of the MVP backend · 10,005 lines implementation ·
2,591 lines tests · **289 tests passing** (1.0 s, all offline, no LLM)

*Read §§10–13 for what came after session 1.* Session 2 added the MarineRegions
boundary adapter and the REGULATORY domain (§10). Session 3 added the five
agents, the LangGraph orchestration and the LLM provider abstraction (§11).
Session 4 added the NOAA GFS wind forecast (§12). Session 5 verified every data
source, brought the INCOIS PFZ advisory live and settled tide (§13).
Everything above §10 describes the state after session 1 and is still current
except where the later sections say otherwise.*

This document records what was built, why, and the decisions taken while building it.
It is the handover artifact: read this before resuming.

---

## 1. What This Session Covered

| Phase | Outcome |
|---|---|
| Documentation | Design documents **01–22** written (23–30 not started) |
| Phase 0 — foundation | Repo, venv, package layout, test harness |
| Phase 1 — adapters | **five** adapters live and verified: INCOIS ERDDAP, INCOIS GeoServer, CMEMS, MarineRegions, NOAA GFS |
| Phase 2 — canonical schema | Complete, with structural invariants enforced |
| Phase 3 — capability tools | **9 of 12 bound, 8 returning live data** |
| Phase 4 — agents | **All five built** (Planner, Discovery, Geospatial, Risk, Reporting) |
| Phase 5 — LangGraph | **Graph running end to end**, incl. durable human review |
| Phase 6 — geospatial kernel | Geodesy, temporal alignment, derivations, containment (~65 %) |
| — assessment engine | Thresholds, sufficiency, verdicts, confidence, synthesis, REGULATORY (~90 %) |

**Milestones reached.** A live, evidence-backed positive verdict from current data —
`FISHING_SUITABILITY = FAVOURABLE`, driven by a chlorophyll ratio derived from CMEMS
ocean colour — while `SAFETY` correctly refuses for want of an official warning source.

Then, in session 2, a second fully evidenced domain: `REGULATORY`, decided by
point-in-polygon against versioned MarineRegions geometry. A position 60 km inside the
Sri Lankan EEZ returns `RESTRICTED` with the boundary, its dataset version and the
distance to the edge — and that constraint is stated even when safety cannot be
assessed, because it holds whatever the weather does.

---

## 2. Code Map

*Current as of session 5.*

```
backend/orca/
├── schemas/          824 lines   canonical model (a leaf: imports nothing from ORCA)
├── llm/              270 lines   provider abstraction; ORCA runs with none configured
├── adapters/        3483 lines   THE ONLY PLACE WITH PROVIDER KNOWLEDGE
│   ├── erddap.py                 shared ERDDAP protocol (D-28)
│   ├── incois_erddap/            S-01..S-04  Argo, archive SST/chlorophyll
│   ├── incois_wms/               S-06        official PFZ advisory (vector)
│   ├── cmems/                    S-07        waves, currents, SST, chlorophyll, wind obs
│   ├── marineregions/            S-08        versioned maritime boundaries
│   └── noaa_gfs/                 S-11        wind FORECAST (+164 h)
├── tools/            944 lines   capability contracts, registry, composition root
├── geospatial/       662 lines   deterministic kernel
├── assessment/      1144 lines   deterministic rule engine (no LLM, ever)
├── agents/          1244 lines   the five agents + grounding validators
├── graph/           1053 lines   LangGraph state, routing, nodes, runtime
└── cli/              381 lines   `ask` (agent-planned) · `query` (fixed slice)

config/    datasets.json · boundaries.yaml · thresholds/*.yaml · staleness.yaml · tls/
data/      boundaries/<version>/  captured snapshots -- GIT-IGNORED, regenerate
scripts/   capture_datasets.py · capture_boundaries.py · check_sources.py
tests/     unit/ adapters/ agents/ graph/ fixtures/upstream/<source>/
```

**Layering rule.** `agents → tools → adapters → source`. Nothing above `adapters/`
knows a URL, a credential, ERDDAP selector syntax or that Zarr exists. This was
maintained throughout and should be enforced by an import-linter contract when
`agents/` lands (`18_REPOSITORY_STRUCTURE.md` §1). `assessment/` does not import
from `adapters/` either: both read `config/boundaries.yaml`, each taking the
section it owns (D-18).

---

## 3. Findings That Changed the Project

These came from touching live services. Several contradict the original data audit.
Full detail: `03_DATA_SOURCE_MATRIX.md` §14–15.

### 3.1 INCOIS ERDDAP — access confirmed, currency corrected

The audit called it a *"confirmed viable programmatic ocean-data backbone."* Access is
confirmed. **Most datasets are historical archives.**

| Dataset | Coverage ends | Verdict |
|---|---|---|
| `incois_argo_10d_VAM` / `10day_McCreary` | 2026-07-30 | current; 10-day subsurface means |
| `incois_argo_mnt_*` | 2026-07-15 | current; monthly |
| `NOAA_AVHRR_AMSR_datasets` (SST) | 2011-10-04 | archive |
| `incois_oceansat2_datasets` (CHL) | 2020-05-01 | archive |
| `ascat_daily_datasets` (wind) | 2023-05-21 | archive |

| ID | Finding |
|---|---|
| **F-1** | **Incomplete TLS chain.** The server sends only its leaf certificate; the `GlobalSign RSA OV SSL CA 2018` intermediate is absent (`openssl s_client -showcerts` → chain length 1). macOS/curl succeed via AIA fetching; `certifi` and any Linux container **fail**. Handled with the OS trust store plus a runtime-generated bundle. Verification is never disabled. |
| **F-2** | `NOAA_AVHRR_datasets` — the only current SST source (to 2026-08-11) — publishes **latitude as array indices 0–399** despite `units=degrees_north`. Unusable. |
| **F-3** | That same dataset **dropped out of the catalogue mid-session** (19 → 18 datasets). Datasets can disappear at runtime. |
| **F-4** | `AMSR2_3day_Global` fails the same axis check and reports coverage ending 1915. |
| **F-5** | Raw ERDDAP selectors are rejected by the **servlet container** with an HTML 400 before ERDDAP parses them; `[` `]` must be percent-encoded. |
| **F-6** | **No current chlorophyll source** on this ERDDAP. |
| **F-7** | The 1° Argo grid puts the nearest valid node **96 km** from Kochi; coastal cells are null. |

### 3.2 CMEMS — the audit's `AUTH REQUIRED` status was wrong for our path

| Endpoint | Unauthenticated |
|---|---|
| STAC catalogue | **200** — 307 products enumerated |
| ARCO store `.zmetadata` | **200** |
| ARCO data chunk `VHM0/0.0.0` | **200**, 521,648 bytes |

`AUTH REQUIRED` appeared not to hold for the ARCO object store. **This was later shown
to be only partly true — see §3.3.** Datasets bound (ids read from the public STAC
catalogue, not guessed):

| Capability | Dataset | Coverage |
|---|---|---|
| waves | `cmems_mod_glo_wav_anfc_0.083deg_PT3H-i_202411` | → **2026-09-12** (forecast) |
| currents | `cmems_mod_glo_phy_anfc_merged-uv_PT1H-i_202211` | → **2026-09-11** (forecast) |
| SST | `METOFFICE-GLO-SST-L4-NRT-OBS-SST-V2` (OSTIA) | → 2026-09-01 |
| chlorophyll | `cmems_obs-oc_glo_bgc-plankton_nrt_l4-gapfree-multi-4km_P1D_202311` | → 2026-08-31 |
| wind | `cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H_202207` | → 2026-09-01 |

**The wave and current products are forecasts covering tomorrow.** This is the only
source in the project that can answer a question about the future. **Wind is an
observation product with no forecast horizon** — a wind forecast still requires IMD or
another NWP source.

| ID | Finding |
|---|---|
| **F-8** | Coastal cells are land-masked in the wave model; Kochi returns nothing. Nearest valid ocean cell is 10.3 km offshore. |
| **F-9** | `VHM0` is `int16` with `scale_factor = 0.01`; raw reads are wrong by 100×. |
| **F-10** | Zarr **omits all-fill chunks**. A missing chunk must read as *no data*, never `0.0` — which would present as a calm sea. |
| **F-11** | No scalar wind speed is published, only components. |
| **F-12** | OSTIA publishes SST in **kelvin**. Assuming °C reports ~301 °C for a tropical sea. |

### 3.3 CMEMS access is only partly reliable without credentials — correction to §3.2

Late in the session, data chunks that had returned 200 began returning `403
AccessDenied`. End-of-session state:

| Product | Bucket | Data chunk | Across the session |
|---|---|---|---|
| Waves, currents (analysis/forecast) | `arco-time-015` | **200** | reliable throughout |
| SST (OSTIA) | `arco-time-045` | **403** | worked, then intermittent |
| Chlorophyll, wind (observation L4) | `arco-time-044/050` | **403** | worked, then denied |

| ID | Finding |
|---|---|
| **F-13** | A denied request and a nonexistent key return an **identical** `AccessDenied` body (verified with a deliberately nonsensical key). Status and body **cannot distinguish "missing chunk" from "denied"** — a real ambiguity, because Zarr legitimately omits all-fill chunks. |
| **F-14** | The same chunk returned 200 early and 403 later, pointing to throttling or an egress quota rather than a static policy. |

**This invalidates the §3.2 correction.** The audit's original `AUTH REQUIRED` was closer
to correct. Forecast products are usable unauthenticated today; observation products are
not reliable. **Obtaining CMEMS credentials is now a priority action.**

The system behaved correctly under the change without any code alteration: it returned
`AUTH_REQUIRED`, did not retry, did not silently substitute, fell back to the INCOIS
archive, flagged it `STALE_DATA`, and issued `INSUFFICIENT_EVIDENCE` rather than a
verdict. That is the designed behaviour working under an unplanned upstream change.

---

## 4. Design Decisions

ADR-style. `24_ENGINEERING_DECISIONS.md` was never written; this section partially
serves that purpose until it is.

### D-1 · A focused Zarr v2 reader instead of xarray + zarr + fsspec
**Context.** CMEMS publishes ARCO (Zarr) stores over HTTP.
**Decision.** ~200 lines reading the v2 layout directly, with `numcodecs` for blosc.
**Alternatives.** `xarray + zarr + fsspec + aiohttp`.
**Rationale.** A point query costs 2–3 chunk fetches. The full stack brings an async
layer and much looser control over error mapping — and every failure here must become a
canonical ORCA code. Chunk caching and windowed reads were straightforward to add.
**Consequences.** We own the decode path (scale/offset, fill, chunk-omission), which is
covered by tests. If CMEMS moves to Zarr v3 this needs revisiting — `zarr.json` already
returns 403 while `.zmetadata` returns 200.

### D-2 · TLS: OS trust store, then a generated bundle; never disable verification
**Context.** F-1 — a source that works on a laptop would fail in a container.
**Decision.** `truststore` first, then a bundle of certifi roots + the tracked 1.5 kB
intermediate, generated at runtime and invalidated by mtime.
**Rationale.** Vendoring a copy of certifi's root store would go stale silently.
Disabling verification was never an option for a system that cites authorities.

### D-3 · Presence-based factors come from the tool *outcome*, not a sentinel value
**Context.** `official_warning_status` is not a number. Encoding "no warning" as `0.0`
made it indistinguishable from "we could not check".
**Decision.** `EvidencePool.status` is populated from the envelope: `NO_ACTIVE_WARNING`
→ `{active: False, checked: True}`; `AUTH_REQUIRED` leaves it unset.
**Consequence.** "Could not check" can never become "nothing in force". A test asserts
this (`test_unchecked_warnings_do_not_become_no_warning`).

### D-4 · Time-aware source selection, not first-match fallback
**Context.** INCOIS SST ends 2011; CMEMS is current. Both flag `STALE_DATA` for a
future query.
**Decision.** Try the primary; fall back on transport failure, `NO_DATA`, or
unusability for the requested time. When *every* source is degraded, take the one whose
`valid_time` is closest to the request. Never fall back on `AUTH_REQUIRED`.
**Rationale.** A 2011 archive value is not equivalent to last week's just because both
are flagged stale. A credential problem is not fixed by silently switching authority.
**Consequence.** The switch is recorded in `source_resolution`, on each `Provenance`,
and stated in the answer.

### D-5 · Ageing is asymmetric and configured per parameter
**Context.** A cadence-derived symmetric window refused a 3-day-old ocean-colour
composite that is normal input to a productivity judgement.
**Decision.** A value informs the period *after* its valid time, never before.
`config/staleness.yaml` sets `usable_age_days` per parameter: wind 0.25 d, ocean colour
4.0 d.
**Rationale.** Different variables age at different rates for different purposes. A
two-day-old wind observation says nothing about tomorrow; a phytoplankton field persists.
**Status.** The numbers are mine and are labelled `SCIENTIFIC_VALIDATION_REQUIRED`.
They need domain review.

### D-6 · Chlorophyll is expressed comparatively, never absolutely
**Context.** `12_RISK_AND_RECOMMENDATION_SPEC.md` §5.3 forbids "chlorophyll is high" —
it implies a standard ORCA has not validated.
**Decision.** The assessment factor is `chlorophyll_ratio_to_local_median`, computed
over valid cells within 100 km of the point (877 cells in the Kochi run), with a full
derivation record.
**Consequence.** This is what produced the first live verdict. It also means fishing
suitability needs a *field*, not a point — the adapter grew `fetch_local_field`.

### D-7 · Units are read from the source and converted explicitly
**Context.** F-12 (kelvin), plus INCOIS publishing `degs`, `Degree C`, `milligram m-3`.
**Decision.** `schemas/units.py` with an alias table and an explicit conversion
registry. An impossible conversion **raises**.
**Rationale.** Silently returning an unconverted number would put a kelvin value into a
Celsius threshold comparison. Failing loudly is correct.

### D-8 · Derivations belong to the kernel, never to adapters
**Context.** CMEMS publishes wind and current components but no scalar speed.
**Decision.** `geospatial/derive.py` computes speed/direction and ratios, each carrying
`method`, `method_version`, input provenance ids and params.
**Rationale.** A derived number must be recomputable from its record. Putting the
arithmetic in an adapter would bury it under provider-specific code.

### D-9 · Search outward for the nearest valid ocean cell
**Context.** F-8 — Kochi's cell is land-masked in the wave model.
**Decision.** Search outward to a configurable radius (default 60 km), return the
nearest valid cell, flag `INSUFFICIENT_COVERAGE`, and report the distance.
**Rationale.** A fisher is offshore anyway. Reporting "no data" for a coastal query
would be technically true and practically useless — but the offset must be visible.

### D-10 · Structural guards in the schema, not conventions in prose
Enforced at construction: only `SAFETY` may return `UNSAFE`; `REGULATORY` must use its
own vocabulary; a derived value without a derivation record is rejected; an envelope
with an unresolved `provenance_id` is rejected; a material claim without evidence is
rejected; a `Recommendation` cannot be marked as an official advisory.
**Rationale.** These are the project's core promises. A promise enforced by a validator
survives refactoring; one written in a document does not.

### D-11 · Gaps are scoped per domain
A missing wave forecast is a `SAFETY` gap. Listing it under `FISHING_SUITABILITY` is
noise, and a factor that produced a usable driver is not simultaneously "not evaluated".

### D-13 · `403` is a failure, never "no data"
**Context.** F-13. Zarr omits all-fill chunks, so a missing chunk is normal and must read
as absent. On the CMEMS buckets a missing key returns `403 AccessDenied` — identical to a
genuine denial.
**Decision.** `404` reads as absent. `403` raises.
**Alternative considered and rejected.** Treating `403` as absent. I implemented this
first, because it made chlorophyll work again — then reverted it. The two cases are
indistinguishable, so the heuristic would silently discard real observations, and a
land-masked or denied sea would read as a calm one.
**Consequences.** Availability is lower: a throttled chunk fails the query instead of
degrading. Correctness is preserved, which is the right trade for a system that makes
safety statements. Credentials remove the ambiguity entirely.

### D-12 · Upstream fixtures are recorded, never hand-authored
`tests/fixtures/upstream/` carries capture dates. A hand-written fixture would make the
adapter suite test a fiction.

---

## 5. Deviations From the Design Documents

| Document | Deviation | Reason |
|---|---|---|
| `03` §5/§7 — CMEMS `AUTH REQUIRED` | Partly wrong in both directions: forecast products need no credentials, observation products are unreliable without them | §15.5; the audit was closer to correct than my first correction |
| `03` §5 — ERDDAP "viable backbone" | True for access, not currency | Verified live; recorded in §14 |
| `22` §7 — "guaranteed floor" of 4 live capabilities | Both weaker (ERDDAP archives) and stronger (CMEMS unauthenticated) than recorded | §15.4 |
| `04` §3.7 — chlorophyll bands | Factor is a ratio to local median, not a raw value | `12` §5.3 forbids absolute language |
| `11` §8.2 — cadence-derived validity | Replaced by asymmetric, per-parameter policy | D-5 |
| `18` §1 — import-linter contracts | Not yet configured | `agents/` does not exist yet |

---

## 6. Open Decisions Needing Input

**O-1 · RESOLVED (session 3).** `official_warning_status` no longer blocks a
SAFETY verdict; its absence **caps** the verdict at `MARGINAL` instead. See D-26
and `config/thresholds/small_craft_v0.1.yaml`. ORCA can now say "conditions look
marginal", and can never say favourable, when it could not check for a warning.
IMD credentials remain the fix; this is the honest interim.

**O-2 · Threshold values are unvalidated.** `small_craft_v0.1` and `fishing_v0.1` are
engineering parameters, surfaced in every answer as
`SCIENTIFIC_VALIDATION_REQUIRED`. They need review against Indian marine safety guidance.

**O-3 · Staleness tolerances are unvalidated** (D-5).

**O-6 · A narrow intent still triggers a full domain assessment.** The Planner
narrows `warning_lookup` to `official_warning_status` alone and plans one
capability — but the SAFETY *domain* still evaluates against the whole
`small_craft_v0.1` required set, so the answer lists `significant_wave_height`
and `wind_speed` as `NOT_RETRIEVED` when they were deliberately never requested.
The output is truthful and the refusal is correct, but it reads as noise: it
reports absence for things nobody asked for.

Two defensible resolutions. (a) A warning lookup should report warning status
and issue **no** SAFETY verdict at all — the user asked a lookup question, not
for an assessment. (b) `not_evaluated` should distinguish *not planned* from
*planned and not retrieved*, which is a smaller change and keeps the domain
assessment intact.

*Current behaviour: (b) is not implemented; everything unplanned shows as
`NOT_RETRIEVED`.* This needs a product decision about what a lookup question
should return.

**O-5 · The boundary implication table needs legal review** (D-18). It encodes the
ordinary reading of UNCLOS for a fishing vessel — a coastal state controls fishing in its
own EEZ, foreign vessels need authorisation. It does **not** encode bilateral agreements,
traditional fishing rights, the India–Sri Lanka arrangements, or any licence a particular
vessel holds. It is surfaced in every answer as `LEGAL_REVIEW_REQUIRED`. The alternative
— returning `UNKNOWN` for every foreign jurisdiction until a lawyer has signed off — is
defensible but throws away the domain's most useful output.
*Current behaviour: the reading is applied and its status is stated.*

**O-4 · `NOAA_AVHRR_datasets` calibration.** Its latitude axis could be calibrated
against `NOAA_AVHRR_AMSR_datasets` over their overlapping period (1981–2011), which
would recover a current INCOIS SST source. The dataset was unloaded from the server
when attempted. Worth retrying.

---

## 7. Credential and Verification Backlog

| Item | Status | Blocks |
|---|---|---|
| IMD registration | not started | warnings, cyclone, lightning, wind forecast — and therefore **any safety verdict** |
| INCOIS WMS verification from an unrestricted network | not done | PFZ |
| **CMEMS credentials** | **priority — not started** | reliable SST, chlorophyll, wind; removes the 403 ambiguity (F-13) |
| MOSDAC registration | not started | P1 enhancement only |
| VLIZ / MarineRegions licence review | not started | nothing — CC-BY 4.0 attribution is carried; `14` §"terms" still wants a review |
| Legal review of the boundary implication table | not started | nothing — the table is applied and labelled `LEGAL_REVIEW_REQUIRED` (O-5) |
| A source for restricted / naval zones and MPAs | none identified | the one gap that could turn a `PERMITTED` into something else (§10.5) |

IMD is the critical path. Everything else degrades explicitly.

---

## 8. Next Steps

*Rewritten at the end of session 5, against the problem statement rather than
against the roadmap alone (§14 explains why the two differ).*

1. **IMD registration.** The only remaining SOURCE blocker. It gates official
   warnings, lightning and cyclone tracks — three of the four unbound
   capabilities and two of the problem statement's example queries. It is
   paperwork, not engineering, and everything already degrades correctly around
   it. Start it before writing more code.
2. **Conversational API + working multi-turn.** The platform is specified as
   conversational and currently answers one question from a CLI.
   `session_context` is read but never written, so a second turn inherits
   nothing (§14). This unlocks three named capabilities at once.
3. **Multilingual.** Its own bullet in the problem statement, with emphasis on
   Indian regional languages, and currently at zero — `language` is plumbing
   that is always `"en"`. Cheaper than it looks: the deterministic template
   composes answers from structured fields, so this is a lexicon problem rather
   than a model problem, and `06` §7.2 already forbids translating numbers or
   quoted official text.
4. **Geofencing notifications.** Nearly free: distance-to-boundary and a
   `near_boundary` flag are already computed on every run. Turning that into a
   proximity notification is small, and it is a named deliverable.
5. **Map and charts.** Named twice in the problem statement. The layer
   descriptors and evidence already exist to render.
6. **A second wind source.** S-11 is the only wind forecast and is
   intermittently slow (§13.4); when it fails SAFETY loses a required input.
7. **Persistence (`db/`).** Closes the Phase 5 gate and enables the offline
   replay mode `16` §4 wants for a blocked venue network.
8. **Retire `cli/query.py`.** The graph CLI supersedes it; keeping both invites
   drift.
9. **Route optimisation**, and **documents 23–30**.

## 9. How to Run

```bash
python3 -m venv .venv
./.venv/bin/pip install pydantic httpx certifi truststore numcodecs numpy pyyaml \
                        pytest langgraph

./.venv/bin/python -m pytest tests -q          # 289 offline tests, no network, no LLM

./.venv/bin/python scripts/capture_boundaries.py   # REQUIRED once: boundary snapshot
./.venv/bin/python scripts/capture_datasets.py     # live INCOIS metadata capture
./.venv/bin/python scripts/check_sources.py        # live source audit -- run this first
```

`check_sources.py` exercises every capability against a real position and prints
what each source actually did. **Run it before believing any claim in this
document about what works.** Try `--lat 15.5 --lon 81.5` for a position inside
the current PFZ issue.

`data/boundaries/` is git-ignored, so a fresh clone must run
`capture_boundaries.py` before the REGULATORY domain can decide anything; the
adapter says exactly that when the snapshot is absent.

**Ask ORCA a question** — a Planner decides what to retrieve:

```bash
./.venv/bin/python -m backend.orca.cli.ask "is it good for fishing near Kochi tomorrow morning?"
./.venv/bin/python -m backend.orca.cli.ask "am I inside the Indian EEZ near Kochi?"
./.venv/bin/python -m backend.orca.cli.ask "is there a warning in force right now?"
```

Watch the PLAN block change between them: the fishing question plans six tools
and declares five gaps, the boundary question plans one, and the warning lookup
plans **none** — its only source needs credentials — and says so.

**The fixed vertical slice** (hardcoded orchestration, retained for comparison
until it is retired — §8 step 3):

```bash
./.venv/bin/python -m backend.orca.cli.query
./.venv/bin/python -m backend.orca.cli.query --when 2011-06-15T00:30:00
```

The second invocation targets a date inside ERDDAP's archive coverage and shows
the pipeline producing a verdict from historical data — useful for demonstrating
the reasoning path independently of current data availability.

**No LLM is required.** With `ORCA_LLM_PROVIDER` unset, ORCA plans from
deterministic tables and answers from a grounded template (D-21). Setting it
(see `.env.example`) adds fluency; it cannot change a number or a verdict.

**ORCA output is not an official advisory. Follow IMD and INCOIS bulletins.**

---

## 10. Session 2 — MarineRegions and the REGULATORY Domain

This was §8 step 1. It is the only domain that needs no forecast, no credentials and no
network at query time, which is why it was taken next.

### 10.1 What was built

| Piece | What it does |
|---|---|
| `geospatial/topology.py` | Even-odd ray casting with hole exclusion, antimeridian normalisation, geodesic distance to the nearest boundary edge, and a flat ring index with per-ring bbox prefiltering. 245 lines, no geometry dependency. |
| `adapters/marineregions/` | WFS 2.0 client (capture only), snapshot writer and reader, and the boundary adapter. 932 lines. |
| `tools/boundaries.py` | `get_maritime_boundaries` — the 7th P0 tool. |
| `assessment/jurisdiction.py` | Home-vs-foreign placement and the configured implication table. |
| `assessment/regulatory.py` | Containment → `PERMITTED` / `RESTRICTED` / `PROHIBITED` / `UNKNOWN`. |
| `scripts/capture_boundaries.py` | Live capture → `data/boundaries/<version>/`. |
| `config/boundaries.yaml` | Source, snapshot region, per-type sources, and the implication table with its validation status. |

56 new tests (122 total, 0.25 s, all offline). Two recorded upstream fixtures — real
national polygons, 48 kB — plus a capabilities excerpt.

**Live behaviour, verified this session.** 60 km inside the Sri Lankan EEZ →
`RESTRICTED`, high confidence, with the feature name, `v12 (2023)` and the distance to
the edge. 2.3 km from the India–Sri Lanka boundary in Palk Bay → `PERMITTED` but
confidence capped at medium and the proximity stated. Beyond every EEZ → `UNKNOWN`, not
`PERMITTED`. East of 90 E → `INSUFFICIENT_COVERAGE`, refusing rather than answering.
A boundary query costs 12–20 ms against 458,706 vertices.

### 10.2 Findings

Full detail in `03_DATA_SOURCE_MATRIX.md` §16. The four that changed the design:

*Numbering note.* These findings were authored as F-13–F-18 on the feature branch,
which collided with the CMEMS `403` findings (§3.3) already holding F-13/F-14 on
`main`. The MarineRegions set was renumbered **+2 to F-15–F-20** on merge; the CMEMS
pair keeps F-13/F-14 because D-13 and §7 cite them. Read PR #1 accordingly.

| ID | Finding |
|---|---|
| **F-15** | The layers declare `urn:ogc:def:crs:EPSG::4326`, so CQL `BBOX` is read **latitude first**. The first capture asked for the Indian Ocean and got Svalbard and the Russian Arctic — a plausible-looking, entirely wrong, non-empty result. |
| **F-17** | `eez_12nm` and `eez_24nm` are **bands from the baseline, not nested discs**. 5 NM offshore is inside the territorial sea and outside the contiguous zone; 20 NM offshore is the reverse. Treating them as nested is wrong in both directions. |
| **F-18** | `eez_internal_waters` publishes **nothing for Sri Lanka**. "Outside every internal-waters polygon" there is a gap in the source, not a fact about the point, and is downgraded to *not evaluated for this jurisdiction* (D-17). |
| **F-19** | The service publishes **no version field** — only a release year inside the layer title. The capture parses it and **fails** rather than writing geometry that cannot be cited. |

### 10.3 Design decisions

#### D-14 · A versioned local snapshot, not a query-time WFS call
**Context.** `04` §3.11 specifies a preloaded, versioned PostGIS snapshot. There is no
PostGIS in this project yet.
**Decision.** Capture to `data/boundaries/<version>/`: a manifest with provenance and
per-feature attributes, plus one flattened `.npz` of full-precision geometry per layer.
**Rationale.** Version binding is the point. A run that said "inside the Indian EEZ" in
March must still be checkable in September against the geometry it actually used, and a
live WFS call cannot promise that. It also means the REGULATORY domain keeps working
when the network does not.
**Consequences.** `data/boundaries/` is git-ignored, so a fresh clone must run the
capture; the adapter says exactly that when the snapshot is absent. Loading is 2 ms.

#### D-15 · Coverage is a declared region, and outside it the answer is refusal
**Context.** A snapshot holds the features intersecting a bbox. Outside that bbox,
"inside no boundary" is indistinguishable from "we did not look".
**Decision.** The snapshot records its region. A query outside it returns
`INSUFFICIENT_COVERAGE` and no containment result at all.
**Rationale.** Same principle as D-3: *could not check* must never become *nothing
found*. The failure this prevents is a vessel being told it is in international waters
because the snapshot stopped at 90 E.

#### D-16 · Boundary types are evaluated independently; the worst governs
**Context.** F-17 — the zones are bands, not a hierarchy.
**Decision.** Each type is tested separately and mapped through a configured implication
(`home` / `foreign` / `none`); the most constraining outcome governs. Never averaged,
never inferred from a neighbouring type.
**Consequence.** Inside a foreign territorial sea is `PROHIBITED` even though the
surrounding EEZ alone would be `RESTRICTED`.

#### D-17 · A layer with no feature for this jurisdiction cannot say "outside"
**Context.** F-18.
**Decision.** After containment, the adapter checks whether the governing EEZ's
sovereign appears at all in each other layer. If not, that type is flagged and the
assessment lists it as `INSUFFICIENT_COVERAGE`, not as unconstrained.
**Rationale.** The error is asymmetric: an unchecked internal-waters polygon can only
make the answer more restrictive, never less. Reporting it as "outside" would understate
a restriction, which is the direction that gets someone arrested.

#### D-18 · The geometry is a fact; what it means is a legal judgement, and they live apart
**Context.** "Inside another state's EEZ ⇒ needs authorisation" is not something an
adapter should assert, and not something an engineer should encode as a constant.
**Decision.** The adapter reports only what the source publishes — sovereign, territory,
ISO code, distance. `config/boundaries.yaml` carries the implication table, marked
`LEGAL_REVIEW_REQUIRED`, and `assessment/jurisdiction.py` reads it. The adapter and the
assessment read different sections of the same file and do not import each other.
**Consequence.** Every regulatory answer surfaces `LEGAL_REVIEW_REQUIRED`, exactly as
threshold-based answers surface `SCIENTIFIC_VALIDATION_REQUIRED`.

#### D-19 · An unevaluated boundary type is named in every answer
**Context.** `04` §3.11 rule 2 — an EEZ polygon is not a fishing regulation zone.
**Decision.** `boundary_types` defaults to every type ORCA has a policy for, including
the four with no source (MPA, restricted zone, fishing regulation zone, seasonal
closure). Each returns `DATASET_UNAVAILABLE` and appears under `not_evaluated`.
**Rationale.** An answer that quietly omitted restricted zones would read as "you are
clear". A `PERMITTED` verdict with unchecked restrictions is therefore capped at medium
confidence — an unchecked naval exercise area can only make things worse.

#### D-20 · A regulatory constraint outranks a safety refusal in the headline
**Context.** `synthesise` answered a safety-input gap with `CANNOT_ADVISE` before
looking at any other domain, which would have buried a `RESTRICTED` or `PROHIBITED`
result — and today safety *always* refuses, for want of IMD credentials.
**Decision.** Regulatory constraints are settled first, per `12` §8's priority order.
The headline then adds that conditions could not be assessed, and the disposition stays
`BLOCKED`.
**Rationale.** A boundary holds whatever the weather does. Naming it is useful even when
nothing else can be said.

### 10.4 Deviations from the design documents

| Document | Deviation | Reason |
|---|---|---|
| `04` §3.11 / `09` §4.2 — PostGIS snapshot | Flat `.npz` arrays + JSON manifest | D-14; no PostGIS in the project yet. The interface is unchanged and the store is swappable |
| `06` §476 — `REGULATORY PERMITTED confidence high` | `PERMITTED` is capped at **medium** while restriction-bearing types are unevaluated | D-19 |
| `12` §11 — category table | No category is defined for `REGULATORY RESTRICTED`; mapped to `PROCEED_WITH_CAUTION` | Needing another state's authorisation is neither a prohibition nor "proceed with context" |
| `04` §3.11 — `international_boundary` as a boundary type | Not configured. `eez_boundaries` is a **line** layer; containment is undefined for it | Distance to the nearest EEZ edge already answers "how far am I from the line", and is reported |

### 10.5 What this domain still cannot do

* **No restricted or naval zones, no MPAs, no fishing regulation zones, no seasonal
  closures.** These are the restrictions most likely to bite, and none has a configured
  source. Every answer says so.
* **The monsoon fishing ban is not a polygon.** It is a dated legal instrument issued per
  state, and no boundary dataset can express it.
* **Bilateral arrangements are not encoded** — including the India–Sri Lanka
  arrangements, which is exactly the water where the geometry is most useful.
* **No vessel context.** A licence, a registration or a permitted gear type would change
  the answer, and ORCA holds none of it.
* **No land mask.** "Outside every EEZ" is equally true of the high seas and of a street
  in Kochi. The domain says so in the evidence statement rather than picking one — but a
  coarse land mask (`18` §6 already reserves `data/landmask/`) would let it distinguish
  them, and that is worth doing.
* **East of 90 E is uncovered** by the default snapshot (F-20).

---

**ORCA output is not an official advisory. Follow IMD and INCOIS bulletins.**

---

## 11. Session 3 — Agents and the LangGraph Orchestration

Phase 4 and Phase 5. The CLI previously hardcoded the orchestration a Planner is
meant to decide; it no longer has to.

### 11.1 What was built

```
backend/orca/
├── llm/              ~260 lines   provider abstraction
│   ├── provider.py               LLMProvider protocol, registry, UnavailableProvider
│   ├── providers/                one module per provider (lazy SDK import)
│   └── usage.py                  token ledger + budget enforcement
├── agents/          ~1180 lines   judgement layer
│   ├── base.py                   budgets, AgentResult, structured failure
│   ├── contracts.py              Plan, RetrievalReport, ValidationReport, AlignmentReport
│   ├── planner.py                intent, domain/evidence tables, plan + re-plan
│   ├── discovery.py              step execution, widening policy, coverage report
│   ├── geospatial_agent.py       alignment + derivation, AlignmentReport
│   ├── risk.py                   per-domain assessment + validated rationale
│   ├── reporting.py              narrative, claims, template fallback
│   └── validators/grounding.py   numeric fidelity, official language, absence guard
├── graph/           ~1100 lines   orchestration
│   ├── state.py                  OrcaGraphState + reducers
│   ├── runtime.py                OrcaRuntime carried through config, not state
│   ├── routing.py                conditional edges, Send fan-out
│   ├── build.py                  graph assembly
│   ├── events.py                 node events (never chain-of-thought)
│   └── nodes/                    context, planning, retrieval, validation,
│                                 analysis, assessment, delivery
├── tools/registry.py             catalogue + per-environment enablement
├── tools/live.py                 composition root binding adapters
└── cli/ask.py                    graph-driven CLI
```

`config/` gains nothing; `.env.example` was added (it was specified in `19` but
missing).

### 11.2 The central decision: ORCA runs without a model

**D-21 · No LLM is required, and the deterministic path is first-class.**
**Context.** Phase 4 needs "an LLM provider configured" and none is. Making the
agents depend on one would have made the whole reasoning layer untestable
offline and undemonstrable without a key.
**Decision.** `LLMProvider` resolves from `ORCA_LLM_PROVIDER`; when nothing is
configured it returns an `UnavailableProvider` whose `available` is `False`.
Every agent consults `use_llm()` and takes a deterministic path otherwise.
**Rationale.** The specification *already* mandates a deterministic fallback at
every LLM site — plan repair (`06` §3.8), template rationale (§6.7), template
answer (§7.8). Making those the default rather than the exception costs nothing
and means an unconfigured deployment produces a complete, grounded, less fluent
answer instead of no answer.
**Consequence.** All 289 tests are offline and model-free. Configuring a model
changes fluency; it cannot change a number or a verdict, and tests assert that.

### 11.3 Where the LLM is allowed to act, and what constrains it

| Site | What the model may do | What stops it doing harm |
|---|---|---|
| Planner intent | Classify into one of nine intents | Enum-constrained; keyword classifier otherwise |
| Planner relevance | **Narrow** the preferred-evidence list | May only select from the list; cannot touch `required`, cannot reach a tool |
| Geospatial summary | Rephrase computed statistics | Given only the statistics; no other input exists |
| Risk rationale | Phrase the engine's verdict | Rejected if it introduces a number or uses reserved official language; engine text stands |
| Reporting narrative | Compose the answer | Numeric fidelity, official-language and absence-as-safety validators; two failures fall to template |

`DOMAIN_MAP` and the evidence requirements are **tables**, and the evidence
tables are read from `config/thresholds/*.yaml` rather than restated — so a
factor added to a threshold set is planned for automatically and the Planner
cannot drift out of step with what the engine will demand.

### 11.4 Findings

| ID | Finding |
|---|---|
| **F-21** | **Re-planning for an unfillable gap is an infinite-ish loop of identical requests.** The first live run re-planned twice for `official_warning_status` (no source at all) and `wind_speed` (tool already answered with stale data), re-issuing the same calls and inflating the evidence count 17 → 23 → 29 with duplicates. A gap is only worth re-planning if some tool yielding it is **available and not yet attempted**; `ValidationReport.actionable_gaps` now carries that, and an unfillable gap degrades the domain instead (`06` §3.8). |
| **F-22** | **`07` §5 routes `BLOCKED` to `finalize`, which delivers the user nothing.** §8's degradation ladder requires BLOCKED to produce "no verdict, explicit statement of what could not be reached". Deviation recorded: BLOCKED routes to `report`, which composes the explanation over assessments that are all `INSUFFICIENT_EVIDENCE`. The grounding validators forbid it from asserting safety, so it explains without ever concluding. |
| **F-23** | A time-independent question ("am I inside the EEZ?") legitimately resolves **no** time window, and the Planner correctly does not ask for one — but the analysis frame still needs an interval. `_window` defaults to the present; time-sensitive intents never reach it without a window because the Planner asks first. |
| **F-25** | **Resolving O-1 did not unblock the demo, because `wind_speed` has no forecast source either.** CMEMS wind is an L4 *observation* product with no forecast horizon (F-11), so a query about tomorrow — the demo's own query — yields `STALE_DATA` and SAFETY still refuses, correctly, since wind is a measurement and does not cap. `03` §7 already names the answer: `get_weather` is specified as S-05 IMD (auth) with **S-11 NOAA as the designated fallback, still PROPOSED**. Building it is the one remaining thing between ORCA and an end-to-end safety verdict, and it needs no credentials. |
| **F-24** | The chlorophyll local-median ratio was derived in the **CLI**, reaching into the CMEMS adapter. `agents/` may never do that, so the derivation moved into `get_chlorophyll` (`tools/` may import both `adapters/` and `geospatial/`). One code path now, and every consumer of the capability gets the same evidence. |

### 11.5 Design decisions

*Numbering note.* Session 2 had reused **D-13**, which session 1 already used for
the CMEMS `403` decision, and its block then collided with session 3's. Resolved
on merge: session 1 keeps D-1–D-13, session 2 shifted to **D-14–D-20**, session 3
takes **D-21–D-25**. Cross-references were updated with them.

**D-22 · The registry is the seam that keeps `agents/` away from `adapters/`.**
It carries a CATALOGUE of pure metadata (name, args schema, evidence yielded) —
all the Planner may see — plus callables bound by the composition root in
`tools/live.py`. A test asserts the plan contains no URL, dataset id or
credential string.

**D-23 · A capability with no source is *declared*, not omitted.**
`mark_unavailable` keeps the tool in the catalogue so the Planner still plans
for it and the answer states what it could not check. This is what produces
"nine tools exist, one is used, four are declared unavailable".

**D-24 · Live objects travel in graph *config*, not graph *state*.**
The registry, provider and budget are not serialisable and must not be
checkpointed. `OrcaRuntime` moves through `config["configurable"]["orca"]`,
which keeps state to plain data that can be replayed for audit.

**D-25 · A branch that fails hard still appends an assessment.**
A missing branch would stall the LangGraph superstep, so a failed domain appends
`INSUFFICIENT_EVIDENCE` (or `UNKNOWN` for REGULATORY, which has its own
vocabulary). The join count always matches the dispatch count.

**D-26 · A missing authority check caps the verdict; a missing measurement still blocks it.**
**Context.** O-1. `official_warning_status` was a required SAFETY factor with no
reachable source, so SAFETY always returned `INSUFFICIENT_EVIDENCE` and every
answer ended in `CANNOT_ADVISE` — including `16_DEMO...` §2 segment 6, which the
demo spec calls "the differentiator".
**Decision.** Threshold sets gain a `capping_factors` tier. A required factor
listed there ceilings the verdict at a named band when missing, instead of
refusing. `official_warning_status` caps at `marginal`.
**Rationale.** An authority check is not a measurement. Without wave height or
wind there is no sea state to assess and ORCA must refuse. Without a warning
check there *is* an assessable sea state — what is missing is the authority that
would have overridden it. Refusing outright withholds usable information;
answering uncapped could say "favourable" while a cyclone warning stands. The
cap is the narrow path between those.
**Consequences.** ORCA can never say favourable without an authority check.
Confidence is capped at medium, the gap is named in every answer, the rationale
states that the verdict is a ceiling, and `Assessment.verdict_capped_by` records
it. The reporting absence-guard treats a capped verdict as *not* assessed, so a
narrative still cannot claim safety. Deviates from `12` §4.1 as written.
**Reversal.** Delete the `capping_factors` block once IMD credentials exist.

### 11.6 Deviations from the design documents

| Document | Deviation | Reason |
|---|---|---|
| `07` §5 — `BLOCKED` → `finalize` | Routes to `report` instead | F-22; §8 requires BLOCKED to explain itself |
| `07` §4 — `nodes/` one module per node | Grouped by stage (7 modules, 16 nodes) | A file holding three ten-line functions is harder to follow than one holding the stage |
| `07` §14 — PostgreSQL checkpointer | `MemorySaver` in tests; no persistence yet | `09_DATABASE_SPEC.md` is not implemented; the interrupt/resume contract is exercised and survives a rebuilt graph |
| `06` §4.7 — LLM re-request on an unsatisfied step | Not implemented | F-21 showed the deterministic widening already covers the cases we have; adding a model call to re-ask an unavailable source would be waste |
| `07` §5 — separate `retrieve` dispatcher node | Dispatch is the conditional edge out of `plan` | Matches §5's own `add_conditional_edges("plan", dispatch_tools, ...)` |

### 11.7 What the graph does that the vertical slice did not

Running the same question through `cli.ask` rather than `cli.query`:

* **The plan changes with the question.** "Is there a warning in force?" plans
  **zero** tools of eleven and declares the one gap; "am I inside the EEZ?" plans
  one; the fishing question plans six and declares five gaps.
* **An unresolved location asks instead of assuming.** No retrieval happens.
* **Domains fan out and rejoin** by `Send`, so only requested domains run.
* **An official warning holds the answer at `human_review`** as a durable
  interrupt; nothing is delivered until a decision is recorded, and the state
  survives the process being rebuilt.

### 11.8 What this layer still cannot do

* **No conflict detection.** `conflict_resolve` is a declared seam that finds
  nothing, because the tool layer selects one source per parameter. Real
  cross-checking needs a second source per capability.
* **No checkpointer persistence.** Interrupt/resume works in-process; surviving
  a real restart needs `09_DATABASE_SPEC.md`.
* **No `ECOLOGICAL` domain** and no P1 RAG, translation or route tools.
* **The gazetteer is a 12-entry placeholder.** Anything outside it asks the user
  rather than guessing, which is the right failure but a narrow one.
* **No import-linter in CI.** The contracts are asserted by
  `tests/unit/test_import_boundaries.py` (80 assertions) rather than at build
  time.

---

## 12. Session 4 — the NOAA GFS wind forecast (S-11)

Built because of F-25: without a wind *forecast* no SAFETY verdict was possible
for any future window, so the demo's central segment could not run. This is the
adapter `03` §7 already specified as the `get_weather` fallback, and it needs no
credentials.

### 12.1 Finding the endpoint — three dead ends

`03` §7 named "NOAA" without an endpoint. The obvious routes are gone:

| ID | Finding |
|---|---|
| **F-26** | **NOMADS OPeNDAP is retired** (NWS Service Change Notice 25-81). `nomads.ncep.noaa.gov/dods` answers **HTTP 200 with an HTML retirement notice**, so a client that trusted the status code would parse the notice as data. This is F-3's lesson again in a new form: a 200 is not a result. |
| **F-27** | `coastwatch.pfeg.noaa.gov` and `upwell.pfeg.noaa.gov`, the long-standing CoastWatch ERDDAP hosts, both **time out**. Other NOAA ERDDAPs (`osmc`, `erddap.aoml`) are up but serve observing-network data, not NWP. |
| **F-28** | The surviving NOMADS **GRIB filter** works, but serves GRIB2. Decoding it needs an `eccodes`/`cfgrib` binary dependency, and a GRIB2 decoder is an order of magnitude more work than D-1's Zarr reader (JPEG2000 and complex packing with spatial differencing). Rejected on both counts. |

**Resolution.** PacIOOS (University of Hawaii, an IOOS regional association)
republishes the NCEP GFS run over **ERDDAP griddap**, which ORCA already reads.
Global 0.5°, hourly steps, **+164 h horizon** when measured on 2026-09-03.

**D-27 · The originating authority and the distributor are both recorded.**
The data is NOAA NCEP GFS; PacIOOS is the host. Provenance names NOAA as
`source`/`organisation` and PacIOOS in `notes` and the licence reference, and
never presents the redistributor as the authority. A system that cites
authorities has to be exact about which one it is citing.

### 12.2 What was built

```
backend/orca/adapters/
├── erddap.py              188 lines  SHARED ERDDAP protocol (new)
├── incois_erddap/client.py 101 lines  was 232 -- now only the INCOIS host
└── noaa_gfs/                          client, bindings, adapter (S-11)
```

**D-28 · The ERDDAP protocol is shared; the host is not.**
ERDDAP is a server product, not a source, and its quirks belong to the software:
the selector characters the servlet container rejects (F-5), the errors returned
under HTTP 200, the "unknown datasetID" body that means a dataset was unloaded
(F-3). Those are now in `adapters/erddap.py` and cannot be fixed for one ERDDAP
and not another. Base URL, TLS and dataset bindings stay per-host — INCOIS keeps
its incomplete-chain workaround (F-1), GFS uses standard TLS. `incois_erddap`
subclasses the shared client and keeps its public API, so its suite was
unchanged and INCOIS returned an identical live value after the refactor.

The adapter emits wind **components**; the kernel derives speed and direction
with a recorded method (D-8), exactly as for CMEMS. GFS publishes no gust in
this dataset, so `wind_gust` stays unavailable and is reported as not evaluated
— never approximated from the mean wind.

### 12.3 A gate that was judging the wrong thing

| ID | Finding |
|---|---|
| **F-29** | **The validate gate ran before derivation, so it judged coverage against values the run had not computed yet.** Derivation lived in `geo_reason`, which runs *after* `validate`, so `wind_speed` was reported as a missing required input in the `ValidationReport` while the assessment immediately went on to use it. The audit artifact contradicted the answer. Not caught earlier because wind was genuinely missing until this session; the GFS forecast made the two disagree visibly. Fixed by deriving in `tool_exec`, where the values are retrieved: `geo_reason` now *reports* derivations rather than performing them, and every downstream consumer sees one set of values. |

### 12.4 Result

For the demo's own question, against live data:

```
tool_exec   get_weather (fallback)   satisfied     <- GFS served a future window
validate    21 value(s); required gaps: official_warning_status
assess_safety   SAFETY = MARGINAL (confidence medium)
ANSWER [PROCEED_WITH_CAUTION]   disposition: AUTO_RELEASE
```

`16` §2 segment 3 wanted "one visible failure and one fallback" — the fallback is
now real and is stated. **SAFETY produces a verdict for tomorrow**, which no
combination of sources could do before.

### 12.5 What this does not fix

* **The demo still cannot show FAVOURABLE fishing beside MARGINAL safety**
  (`16` §2 segment 6). Both domains currently read MARGINAL, driven by a
  chlorophyll ratio of 1.026 — near the middle of its band. The *mechanism* for
  disagreement is built and tested; whether the sea disagrees on demo day is not
  ours to arrange. A recorded run at a location and date where the two genuinely
  diverge is the honest way to show it, which is another argument for replay mode.
* **`wind_gust` has no source.** GFS publishes none here; IMD would.
* **Conflict detection (M-32) is still unbuilt**, though GFS now gives
  `get_wave_conditions` a plausible second source for it.

---

## 13. Session 5 — making the data sources actually work

A source audit rather than a feature: `scripts/check_sources.py` exercises every
capability live and prints what each one actually did. Run it before believing
any claim about what works.

**Before:** 7 bound, 2 returning clean data, 4 declared unavailable.
**After:** 9 bound, 8 returning data, 3 unavailable (all IMD) + 1 declared with
no source (tide).

### 13.1 PFZ is live — and the design's `RASTER_ONLY` branch was not needed

`03` §S-06 had been PENDING VERIFICATION since the audit, because the original
test network could not resolve `services.incois.gov.in`. That host still does not
resolve; **`incois.gov.in/geoserver` does**, and serves 342 layers.

WFS is 403, but **`GetFeatureInfo` returns real `MultiLineString` geometry**, so
PFZ is a *vector* capability. `get_pfz` now answers the problem statement's first
example query against the official product:

```
Where is the nearest PFZ today?
  -> 45.22 km away, issued 2026-09-02
  -> official INCOIS advisory, quoted not recomputed
  -> distance_to_advisory_line v1.0
```

**D-29 · ORCA measures the distance; INCOIS owns the advisory.**
`12` §5.3 reserves "PFZ" for the authoritative product. The distance is a
*derived* value carrying a derivation record that names the official geometry it
was measured against; ORCA never infers a PFZ from SST and chlorophyll, and its
own productivity reasoning keeps a separate name.

**Three outcomes, kept distinct.** Found; checked-and-none-in-range (`NO_DATA`, a
result); outside the issue extent (`INSUFFICIENT_COVERAGE`, we did not look).
`EvidencePool` gained PFZ status ingestion so the middle case can never read as
the last (D-3). Kochi is currently the third case: today's issue starts at
11.64 N and Kochi is at 9.93 N.

| ID | Finding |
|---|---|
| **F-30** | The PFZ layer has **no time dimension**; the issue date is `Year` + `Julian_day` and must be converted. An old or undated advisory is flagged, never presented as today's. |
| **F-31** | Today's issue does not reach Kochi. A fact about the issue, not the point. |
| **F-32** | The GeoServer is intermittently 5xx. Retry handles it; a persistent failure degrades to a declared gap. |
| **F-34** | **`distance_to_ring_km` closes the ring with `np.roll`, which is right for a polygon and wrong for a line.** On a PFZ advisory it invents a segment from the last vertex to the first, which can run hundreds of kilometres across open sea — a test point measured 5.6 km from the phantom segment and 105.6 km from the real line. Added `distance_to_line_km` for open polylines. |

### 13.2 Tide — no source, and that is the answer

The problem statement names tide. Every route was tried (`03` §17.3): UHSLC has a
**Cochin gauge 4 km from the demo point** but its data ends 2026-07-31; CMEMS
publishes a tide product for the Arctic only; the INCOIS `TideGauges` layer
carries station locations, not levels; NOAA CO-OPS is US-only.

**D-30 · A declared capability with no source, rather than a computed tide.**
ORCA will not compute its own tide prediction: without published harmonic
constituents that is an authoritative-looking invented number, which is the one
thing this system exists not to do. `get_tides` is in the catalogue and is named
in every answer that would have used it. Tidal *currents* are already covered —
the CMEMS total-current product includes the tidal component.

This is a deviation from `04`, which lists eleven P0 tools and omits tide even
though the problem statement asks for it.

### 13.3 Chlorophyll was never broken

Investigated because it looked alarming in the audit: `PARTIAL` with
`STALE_DATA` and `INSUFFICIENT_COVERAGE`. It returns 4.77 mg m-3 valid
2026-09-01 with a local-median ratio of 1.026. The adapter flags a two-day-old
composite as stale; `config/staleness.yaml` allows ocean colour four days, and
the assessment uses it. That is the layering working: the adapter reports what
the product is, the domain policy decides whether it is fit for purpose.

### 13.4 Source reliability, as observed

| Source | Observation |
|---|---|
| S-11 NOAA GFS (PacIOOS) | correct but **intermittently slow** (29 s, 88 s, one outright failure in ~10 calls). It is the *only* wind-forecast source, so when it fails the run falls back to a stale CMEMS observation and SAFETY loses a required input. A second NWP source would remove a single point of failure. |
| S-06 INCOIS GeoServer | intermittent 503s between successful calls |
| S-07 CMEMS | chlorophyll consistently slow (13–71 s); the 403 intermittency of §15.5 was not reproduced this session |

### 13.5 Two bugs the live PFZ data exposed

Wiring a real source in immediately surfaced two places where its values were
being quietly discarded. Neither was visible while the source was unavailable.

| ID | Finding |
|---|---|
| **F-35** | **`FISHING_SUITABILITY` did not accept `BULLETIN_PERIOD` as primary evidence**, so the INCOIS PFZ advisory — the most authoritative fishing input ORCA has — was downgraded to `REPRESENTATIVENESS_MISMATCH` and a *derived* chlorophyll ratio drove the verdict instead. Backwards. `DOMAIN_ACCEPTS` now admits bulletins for fishing. |
| **F-36** | **Presence-based factors other than `official_warning_status` contributed nothing at all.** The engine built evidence and a driver for warnings and then `continue`d, so a checked `pfz_advisory` or `lightning` produced no evidence, no driver and no contribution to sufficiency. Replaced with a `PRESENCE_SEMANTICS` table. |

**D-31 · Presence and absence are not symmetric, and the table says so.**
A warning or a lightning strike in force is adverse and its confirmed absence is
reassuring, so both directions band. A PFZ advisory nearby is a positive signal,
but its absence carries **no** verdict weight: INCOIS issues advisories where
conditions warrant, not everywhere every day, so "no advisory here today" is an
editorial fact about the bulletin, not evidence that fishing is poor. Banding
the absence would manufacture an unfavourable finding out of an issuing
decision. The checked absence is still recorded as evidence, so the answer can
say it was looked for.

---

## 14. Where the Project Stands Against the Problem Statement

Recorded because the internal roadmap and the problem statement do not weight
the same things, and the difference is a scoring risk rather than a matter of
taste. `17_IMPLEMENTATION_ROADMAP.md` §4 lists **route optimisation** and
**more than two languages** as explicit MVP *non-requirements*; the problem
statement names both. That scoping was defensible for a vertical slice. As a
submission strategy it needs a deliberate decision, not inheritance.

### 14.1 The ten named platform capabilities

| # | Capability | State |
|---|---|---|
| 1 | Understand natural-language intent | strong |
| 2 | **Detect the language and reply in it — Indian regional** | **absent** |
| 3 | **Contextual multi-turn conversation** | **~5 %** — `session_context` is read, never written |
| 4 | Autonomously discover, retrieve, integrate datasets | strong — five sources live |
| 5 | Spatial / temporal / contextual correlation | strong |
| 6 | Explainable recommendations **with maps, charts, geo-visualisation** | evidence yes; **visuals absent** |
| 7 | **Proactive hazard alerts** | **absent** (and 3 of 4 inputs are IMD-blocked) |
| 8 | **Geofencing notifications** | containment yes; **notification absent** |
| 9 | **Route optimisation / safe navigation** | **absent** |
| 10 | Recommendations with supporting evidence and reasoning | the strongest thing ORCA has |

Four strong, one half, five absent — roughly **45–50 % of the named
capabilities**, and the absences are concentrated in the *conversational* and
*presentational* half of the brief rather than the reasoning half.

### 14.2 The eight example queries

| Query | Answerable |
|---|---|
| Nearest PFZ today | **yes** (session 5) |
| Safe to venture tomorrow morning | **yes** (session 4) |
| Tide, weather and sea conditions | partial — **no tide** (§13.2) |
| Lightning or cyclone alerts | no — IMD |
| Regions with high chlorophyll and favourable SST | partial — point, not region; no map |
| Safest route | no |
| Why has fish productivity declined | no — no historical comparison, no RAG |
| Zones to avoid (hazard or geofencing) | partial — containment yes, notification no |

**Two of eight fully, three partially.** At the start of this conversation it
was zero of eight, so the direction is right; the count is what matters for a
submission.

### 14.3 The agent-count question, which we should expect to be asked

The problem statement *encourages* nine named agents: planning, data discovery,
**weather intelligence**, **ocean analytics**, geospatial reasoning, risk
assessment, **visualisation**, reporting, **user interaction**. ORCA has five,
deliberately: `06` §1 argues an agent exists only where judgement under
uncertainty is required, and that alignment, unit conversion, point-in-polygon
and threshold evaluation are kernel functions with unit tests rather than
"agents".

That argument is sound and the design should stand. But `16` §6's twenty
prepared judge objections **do not include it**, and "why five when the brief
says nine?" is an obvious question. It needs an answer written down: the four
missing names are *functions ORCA performs*, and three of them (weather
intelligence, ocean analytics, visualisation) are deterministic — making them
agents would add ceremony without adding judgement.

---

## 15. Decision Index

Every ADR-style decision in this document, in one place. The IDs collided twice when sessions ran in parallel (§10.2, §11.5); this table is the authority on which is which.

| ID | Decision | Where |
|---|---|---|
| **D-1** | A focused Zarr v2 reader instead of xarray + zarr + fsspec | §4 |
| **D-2** | TLS: OS trust store, then a generated bundle; never disable verification | §4 |
| **D-3** | Presence-based factors come from the tool *outcome*, not a sentinel value | §4 |
| **D-4** | Time-aware source selection, not first-match fallback | §4 |
| **D-5** | Ageing is asymmetric and configured per parameter | §4 |
| **D-6** | Chlorophyll is expressed comparatively, never absolutely | §4 |
| **D-7** | Units are read from the source and converted explicitly | §4 |
| **D-8** | Derivations belong to the kernel, never to adapters | §4 |
| **D-9** | Search outward for the nearest valid ocean cell | §4 |
| **D-10** | Structural guards in the schema, not conventions in prose | §4 |
| **D-11** | Gaps are scoped per domain | §4 |
| **D-12** | Upstream fixtures are recorded, never hand-authored | §4 |
| **D-13** | `403` is a failure, never "no data" | §4 |
| **D-14** | A versioned local snapshot, not a query-time WFS call | §10.3 |
| **D-15** | Coverage is a declared region, and outside it the answer is refusal | §10.3 |
| **D-16** | Boundary types are evaluated independently; the worst governs | §10.3 |
| **D-17** | A layer with no feature for this jurisdiction cannot say "outside" | §10.3 |
| **D-18** | The geometry is a fact; what it means is a legal judgement, and they live apart | §10.3 |
| **D-19** | An unevaluated boundary type is named in every answer | §10.3 |
| **D-20** | A regulatory constraint outranks a safety refusal in the headline | §10.3 |
| **D-21** | No LLM is required, and the deterministic path is first-class | §10.3 |
| **D-22** | The registry is the seam that keeps `agents/` away from `adapters/` | §11.5 |
| **D-23** | A capability with no source is *declared*, not omitted | §11.5 |
| **D-24** | Live objects travel in graph *config*, not graph *state* | §11.5 |
| **D-25** | A branch that fails hard still appends an assessment | §11.5 |
| **D-26** | A missing authority check caps the verdict; a missing measurement still blocks it | §11.5 |
| **D-27** | The originating authority and the distributor are both recorded | §12 |
| **D-28** | The ERDDAP protocol is shared; the host is not | §12 |
| **D-29** | ORCA measures the distance; INCOIS owns the advisory | §13 |
| **D-30** | A declared capability with no source, rather than a computed tide | §13 |
| **D-31** | Presence and absence are not symmetric, and the table says so | §13 |

## 16. Session 6 — persistence and route optimisation (2026-09-03)

**Work Completed:**
- **Persistence:** Wired in `SqliteSaver` in the `backend/orca/api/main.py` FastAPI endpoint using a local `sqlite:///orca.db` to handle thread-level persistence, enabling follow-up questions and conversational memory without needing an LLM agent to summarize the conversation.
- **Route Optimization (A*):** 
  - Integrated `routing.py`'s `a_star_route` algorithm into the LangGraph state.
  - Implemented the routing intent processing within the `geo_reason` step of `analysis.py`. The Planner detects intent, routing intercepts coordinate extraction, runs A* over `OceanField` constraints from CMEMS.
  - Fixed Pydantic serialization limits and `Provenance` schema enforcement for injecting derived routing paths directly into the state's `tool_results`.
  - Added support in `delivery.py` to identify `optimized_route` derived data objects in the tool results, packaging them as GeoJSON `map_layers` so the frontend UI can visualize them.

**Decisions:**
- Made Route Optimization fully deterministic: The system relies solely on the `A*` heuristics evaluated against current `OceanField` values and `geo_reason` orchestrates the graph logic without delegating the routing pathfinding task to an LLM.
- Derivations (e.g. Optimized Route) must be injected explicitly through an `OrcaEnvelope` containing `DerivedResult`, `TemporalRef`, and a valid `Derivation` nested within `Provenance`. Skipping this creates runtime Pydantic validation errors in the `assess_domain` components.

---

## 17. Session 7 — multilingual, geofencing and the HTTP API

### 17.1 Multilingual was scaffolded but not reachable

`i18n/` existed with twelve lexicons and was wired into `ingest` and the
Reporting agent — but a query in Malayalam produced **nothing**: intent
`unknown`, location `None`. Detection worked; everything downstream was
English-only.

| ID | Finding |
|---|---|
| **F-37** | **The gazetteer, the intent keywords and the time expressions were all Latin-only**, so language *detection* succeeded while language *comprehension* failed completely. A translated answer to a question ORCA could not read is worth nothing. |
| **F-38** | `generate.py` ended `return "\\n".join(out)` — a literal backslash-n. Every narrative, **including English**, emitted `\n` as text instead of newlines. |

**D-32 · A language is a YAML drop, including how it is understood.**
The lexicons gained `place:`, `intent:` and `time:` sections alongside the
existing output vocabulary, so a language now carries both halves — what ORCA
says *and* what it can read. `_resolve_location`, `_resolve_window` and
`PlannerAgent.classify` consult the language's own section first and fall back
to the Latin/English path, because people mix scripts and transliterate.
English, Malayalam, Tamil and Hindi are filled; the other eight lexicons
degrade to English rather than failing.

Verified: the same question in four languages resolves to the same location,
window, intent and plan, and returns a narrative in the language asked, with
numbers, units and `IMD`/`INCOIS` untouched (`06` §7.2).

**Known limits, recorded not solved.** Devanagari is shared across Hindi and
Marathi, so script detection maps it to Hindi; the Marathi lexicon is complete
and reachable by passing `language: "mr"` explicitly, but not by detection.
Romanised input ("kochiyil naale") is detected as English.

### 17.4 Ten languages, and a rule that stops the claim drifting

The twelve lexicons were not twelve languages. Eight of them —
`bn gu kn mr or pa te ur` — had **zero translated verdicts**: English content in
a file named for another language, with no `place`/`intent`/`time` at all. A
Bengali query would have been detected as Bengali, failed to parse, and been
answered in English.

| ID | Finding |
|---|---|
| **F-42** | **Eight of the twelve lexicons were shells.** Detecting a language ORCA cannot serve is worse than not detecting it: the question goes unparsed and the answer comes back half-English. Advertising twelve was a false claim. |

Six coastal-state languages were filled properly — **Telugu, Bengali,
Gujarati, Marathi, Kannada, Odia** — matching the states whose fishers the
platform is for. Punjabi and Urdu remain shells and are deliberately *not*
advertised.

**D-35 · Support is all-or-nothing, and the code enforces it.**
`REQUIRED_SECTIONS` names the three comprehension sections and the five output
sections a lexicon must define. `is_supported()` checks them, `detect_language`
consults it, and a script whose lexicon is not ready falls back to English **on
purpose** rather than half-serving. `tests/unit/test_i18n.py` asserts the
invariant per language, so a shell can never be advertised again and a new
language cannot be half-added.

Verified across all ten: place, intent and time resolve from the native script;
the narrative renders in the language asked; numbers, units and `IMD`/`INCOIS`
survive untouched.

Every lexicon carries `_meta.status`. All but English are
`TRANSLATION_REVIEW_REQUIRED` — the verdict vocabulary is safety-critical and
has not been checked by a native speaker.

**The verdict vocabulary is safety-critical** and these translations have not
been checked by a native speaker. `UNSAFE` and `INSUFFICIENT_EVIDENCE` matter
more than fluency; treat the lexicon as `TRANSLATION_REVIEW_REQUIRED` in the
same spirit as the thresholds.

### 17.2 Geofencing — a policy over numbers already computed

**D-33 · Geofence alerts fetch nothing.**
Every boundary run already yields, per type, whether the point is inside and how
far the nearest edge is. `assessment/geofence.py` is a policy over those: an
approach threshold per boundary type, "inside" as the notifiable event for
constraining types, and warnings ordered before cautions.

The rule that matters: **an alert is only raised for a type that was actually
evaluated.** A type with no source produces no alert *and no reassurance* —
silence about a restricted zone nobody checked would read as "you are clear"
(D-3 again, in a new place).

### 17.3 The HTTP API

`backend/orca/api/main.py`. It adds no reasoning; every field it returns was
produced by the pipeline and is provenance-bound.

| ID | Finding |
|---|---|
| **F-39** | The first cut built the tool registry **inside the request handler**, opening a fresh HTTP client per source per call. Moved to a FastAPI `lifespan`: built once, held for the process. |
| **F-40** | The time window was passed as `{"start", "end"}` where the graph expects `{"start_time", "end_time"}`, so a caller-supplied window was silently ignored. |
| **F-41** | No CORS. Every fetch from a browser UI would have failed. |

**D-34 · Language is detected per TURN, not per thread.**
The checkpointer restores the previous turn's language, which would answer an
English follow-up in Malayalam. The problem statement asks for the language of
the query in hand, so detection happens at the API boundary and overrides the
restored value.

Endpoints: `POST /v1/chat`, `POST /v1/chat/stream` (SSE, one event per graph
node — this is what lets a UI show the agents working rather than a spinner),
`GET /v1/health`, `GET /v1/health/sources`, `GET /v1/runs/{thread}`,
`GET /v1/runs/{thread}/provenance`.

Multi-turn is confirmed working end to end: a Malayalam question resolves Kochi
from its own lexicon, and an English follow-up on the same thread carries the
location forward while answering in English.

**322 tests, all offline.** The API tests never start the lifespan, so no test
touches the network.

---

## 18. Session 8 — verifying route optimisation, and dropping RAG

### 18.1 Routing was confidently wrong

Session 6 added A* route optimisation. It passed its tests and returned a
plausible-looking path. It was **broken in the most dangerous way available**.

| ID | Finding |
|---|---|
| **F-43** | **Routes crossed land.** Kochi to Chennai returned a straight line over the Western Ghats and through the middle of Tamil Nadu. Two causes: `cost_function` consulted only `OceanField` objects and **nothing in ORCA ever constructs one**, so the field list was always empty and every point cost `0.0`; and there was **no land mask at all**. A plausible line on a map is the worst failure this system can produce — it is exactly the fabricated-but-credible output every other guard exists to prevent. |

**D-36 · Navigability is a required argument, not an optional refinement.**
`a_star_route` now raises without `is_navigable`. There is no permissive default,
because the permissive default is what produced F-43. Field penalties steer a
route *within* navigable water; the mask is the safety property.

**D-37 · The sea mask is the EEZ polygon we already had.**
An EEZ is a maritime zone: it runs seaward from the baseline and excludes land.
"Inside some EEZ" is therefore a serviceable "at sea" test, using versioned
geometry ORCA already captured — no new source, no new dependency. Built at the
composition root (`tools/live.py`) and carried on `OrcaRuntime`, so `graph/`
receives a callable and still never imports an adapter.

Stated limits: outside the snapshot region and beyond 200 NM everything reads as
not-navigable, so a route there **fails rather than inventing a path**; and it is
a coastline test, not bathymetry — it says nothing about depth or hazards.

**D-38 · Endpoint snapping is bounded.**
Harbours sit on land, so endpoints must move to reach water. Unbounded, that
silently relocated an unreachable destination 7° away — answering a question
nobody asked. Capped at 30 km; beyond that routing refuses.

Verified live: **Kochi to Chennai, 51 waypoints, no waypoint on land** — south
down the Kerala coast, around Kanyakumari, north-east up the Tamil Nadu coast.
Kochi to Mumbai, 61 waypoints, likewise clean.

### 18.2 RAG is out of scope — confirmed, not deferred

Checked because it kept appearing on pending lists. It should not be there:

* **The problem statement never mentions it.** No RAG, document retrieval,
  knowledge base or embeddings. It asks for retrieval of *datasets*.
* **`22_MVP_SCOPE.md` §S-02 lists it as a scope cut**: *"RAG over 30–60 curated
  documents — cut entirely; report 'documentation context unavailable'."*
* **`06_AGENT_SPEC.md` §6.6**: `search_marine_knowledge` is P1, explanatory only,
  may never change a verdict — *"In the MVP the agent has no tools."*

Phase 7 comes off the board. It was never pending work.

---

## 19. Session 9 — hardening pass before the UI

A deliberate review of everything not written in this session. Seven defects,
five of them user-visible, none caught by the 394 tests that were passing.

### 19.1 The route nobody could see

| ID | Finding |
|---|---|
| **F-44** | **The problem statement's own route query classified as `fishing_suitability`.** `_KEYWORDS` is first-match-wins and `\bfish` sat at position 4 while the route pattern sat at 6, so *"the safest route for a fishing vessel"* never reached it. Reordered most-specific-first, with the reason written next to the table. |
| **F-45** | **Route destinations were detected and then discarded.** `"plan a route to Mumbai"` matched the only named place as the ORIGIN and routed from the destination to itself. The destination is now settled first and excluded from origin matching. Destination parsing also handles bare `"to X"`, multi-word names and native scripts. |
| **F-46** | **A route requested without a destination silently produced a safety assessment instead.** Now asks (`clarification_needed = "destination"`). Answering a question nobody asked is the failure this system exists to prevent. |
| **F-47** | **`except Exception: logging.error(...); pass` in `geo_reason`.** Route planning was failing on a `NameError` and the user was told nothing — they asked for a route and got a safety answer. The comment promised a "fallback to straight line", which would have been F-43 all over again. Failure is now a declared gap. |
| **F-48** | **The route was computed, then never delivered.** `map_layers` was attached to the `Recommendation` via `model_copy`, while the API read `map_layers` from graph state, which nothing set. Computed-but-undelivered is indistinguishable from broken. |

**D-39 · Route keywords outrank fishing and safety keywords.**
A query naming a route is asking for one whatever else it mentions. The
ordering is load-bearing, so the table now says so.

**D-40 · A route needs no stated time.** `route_optimization` was in
`TIME_SENSITIVE`, so *"plan a route to Chennai"* demanded a time window before
drawing anything. It means now.

### 19.2 Two implementations of the same feature

| ID | Finding |
|---|---|
| **F-49** | **Geofence alerts were computed twice**, by `assessment/geofence.py` at `evidence_assemble` and by a rival copy inside the `report` node. Because `alerts` uses an `add` reducer, every alert was emitted **twice in two different shapes**, and the second copy did not check whether a boundary type had been evaluated — reintroducing the "silence reads as reassurance" bug D-3 exists to prevent. The duplicate is deleted. |
| **F-50** | `a_star_route` built its `Derivation` by hand instead of through `methods.derivation()`, bypassing the registry that enforces "a method may not change behaviour without a version bump" (D-8). Registered. Its `value` was also the waypoint COUNT; it is now the route length in km, which is a number someone can act on. |

### 19.3 What the review did not find

Worth recording, because it is evidence the invariants hold: no fabricated
values, no silent source substitution, no `assessment/` or `geospatial/` import
of `llm/`, no URL literal above `adapters/`, and the 80 import-boundary
assertions still pass. The schema caught two of my own bad test fixtures
during this session — the provenance join and the `NotEvaluated` type — which
is the structural-guard argument (D-10) working on its author.

**398 tests.** Verified live end to end: Kochi to Chennai returns a 76-point
GeoJSON LineString, 996.6 km, no waypoint on land, marked `advisory_only`, with
a registered derivation and three de-duplicated geofence alerts.

---

## 20. Session 10 — gridded fields for the map

The UI needs fields, not points. Three additions, all behind the existing
layering: adapters gained a grid read, `tools/fields.py` is a capability tool
like any other, and the API exposes it.

### 20.1 A hole must stay a hole

**D-41 · A masked cell reaches the client as `null`, and coverage is reported.**
`fetch_local_field` flattens and drops NaNs, which is right for computing a
median and wrong for drawing a map. `fetch_grid` keeps the grid shape, its
axes, and its holes. Land-masked wave cells and cloud-masked ocean colour are
`null` in the JSON, and every field carries
`cells: {total, valid, coverage}` so a renderer can show partial coverage
honestly instead of implying a complete picture.

Drawn as `0.0`, a masked cell paints a calm, empty sea over data that was never
there. That is F-10 and D-3 restated in pixels, and it is the one way a
beautiful map could undo everything the backend guarantees.

Live: chlorophyll around Kochi returns an 87x87 grid at **coverage 0.502** —
3,769 null cells, which is the coastline and the cloud, visible as gaps.

### 20.2 Endpoints

| Endpoint | Serves |
|---|---|
| `GET /v1/field/{name}` | `wind`, `current` (vector: u, v, speed); `chlorophyll`, `sst`, `waves` (scalar) |
| `GET /v1/fields` | what fields exist, with kind and unit |
| `GET /v1/boundaries` | maritime boundary geometry as GeoJSON, bbox-filtered |
| `GET /v1/boundaries/layers` | the snapshot's layers and version |

**D-42 · The map draws the same geometry the verdict used.**
`/v1/boundaries` serves from the versioned snapshot, not a live WFS call, so
what a user sees is what the REGULATORY assessment was decided against, and the
response carries the `dataset_version` that says which. Rings are **decimated
for display only** — an EEZ ring runs to hundreds of thousands of vertices and
would lock a browser — while containment and distance continue to use full
precision (D-14). The response says so.

**Caching.** Fields are large and slow (wind 7 s, chlorophyll 23 s), so they are
cached on an hourly key — finer than any of the products actually update.

### 20.3 Notes

* GFS is published on longitude 0..360 with latitude **decreasing**, so a range
  selector must be emitted in axis order or the server returns an empty or
  transposed block. Requested as one range call rather than cell by cell, so a
  whole map is a single HTTP request.
* `BoundaryFeature` uses `__slots__`, so `vars()` raises on it — attributes are
  read from `__slots__` instead.
* Field adapters are held for the process in the FastAPI lifespan, like the
  tool registry (F-39).

---

## 21. Session 11 — the interface

`backend/orca/api/static/` — one page, no build step, served at `/ui/`.
MapLibre for the map, a canvas particle layer for vector fields, vanilla JS
for the rest.

### 21.1 What it shows

* **The agent trace, live.** Every graph node streams in over SSE as it
  completes, with the tool name, the source that served it, the canonical codes
  and the duration. This is the differentiator made visible: a dashboard cannot
  show a plan being formed. It needs nothing from the backend that
  `/v1/chat/stream` was not already emitting.
* **Threshold gauges** rather than bare numbers — each driver drawn against the
  favourable / marginal / unfavourable / unsafe bands, with the limiting factor
  marked.
* **Independent verdict cards** that visibly do not merge, each carrying its
  confidence and its "not checked" list.
* **Geofence alerts** with distance, dataset version and `advisory only`.
* **Provenance on click**: any evidence id opens its source, dataset, access
  method and derivation.
* **Source health**: 8 of 12, and the four gaps say why.
* **Field layers**: wind and currents as animated particles, chlorophyll, SST
  and waves as rasters.

### 21.2 The rule the interface inherits

**D-43 · A hole is drawn as a hole.**
Scalar fields rasterise `null` to a transparent pixel and the legend states the
coverage percentage and how many cells are masked. A masked cell painted as
`0.0` would show a calm, empty sea over data that was never collected. Every
guarantee the backend makes could be undone by a renderer that quietly fills
gaps, so it does not.

The same applies to a field that fails: the legend says the layer is **absent,
not empty**, with the reason.

### 21.3 Three bugs, all the same bug

| ID | Finding |
|---|---|
| **F-51** | **A blocked basemap stalled the entire application.** The initial style contained a remote raster source, and MapLibre holds a style unloaded until its sources resolve — so `map.on('load')` never fired, and boundaries, the particle layer and the debug handle never initialised. The style now contains **no remote source**; the basemap is added afterwards as a cosmetic extra with a second tile host as fallback. |
| **F-52** | **`map.on('load')` waits for TILES**, not for the style. Map-dependent setup now keys off `style.load` with an unconditional grace-period fallback, so a throttled or headless renderer cannot stop the app. |
| **F-53** | **An unloaded map style replaced the whole answer with an error.** `drawRunLayers` threw `Style is not done loading`, the exception propagated to the fetch `.catch`, and the user saw "Request failed" instead of a complete, correct verdict that had already arrived. The answer is now rendered first and the map drawn after, isolated. |

All three are the same mistake in three places: **letting the map gate the
product.** The answer is the product; the map is an enhancement. Verified by
running with the basemap entirely unavailable — trace, verdicts, gauges, alerts,
evidence and provenance all render.

### 21.4 Not verified

The **map rendering itself** could not be confirmed in this environment: the
preview pane never completes a MapLibre frame, so the basemap, boundary
polygons, route line and particle layer are unverified visually. The code paths
around them are exercised and fail safe. **Check them in a real browser before
relying on them in a demo.**

### 21.5 Two bugs the interface found in the graph

Driving the graph through a real conversation surfaced defects no single-shot
test could see.

| ID | Finding |
|---|---|
| **F-54** | **Every append-reduced channel accumulated across conversation turns.** `add` is correct for parallel branches within one run, but a checkpointed thread reuses its state, so turn three showed turn one's REGULATORY verdict beside turn three's SAFETY verdict, and three geofence alerts became six. A follow-up question was presenting a stale answer as current. |
| **F-55** | **`run_id` was restored from the checkpoint**, so every turn in a thread shared one id. A run id identifies one RUN; the conversation is the `thread_id`. The audit trail could not tell two answers apart. |

**D-44 · Append-reduced channels are scoped to a run, not to a thread.**
`add_or_reset` clears a channel when its update begins with a sentinel, and
`ingest` clears every accumulator at the head of each turn. The sentinel is
**composable** — `RESET + [event]` clears then writes — because a node that both
resets and writes the same channel would otherwise have its reset silently
overwritten by the later key in the same dict.

Resolved CONTEXT still carries: `session_context` keeps the location and window,
so "is it safe?" after "am I inside the EEZ near Kochi?" still knows where.

| ID | Finding |
|---|---|
| **F-56** | **The stream collapsed parallel retrieval to a single line.** `/v1/chat/stream` emitted only the newest `node_event` per superstep, so a fan-out that ran seven tools at once showed one. The trace exists precisely to show that fan-out. It now emits every new event; the fishing query renders all seven tools. |

### 21.6 Verified in a real browser

Basemap (Esri Ocean Base, bathymetry-shaded, key-free, with CARTO and OSM as
fallbacks), 6 EEZ features, boundary lines, the particle layer, all seven tool
nodes in the trace, three verdict cards, five threshold gauges, three geofence
alerts, nine evidence items with click-through provenance, and a three-turn
conversation that carries context without accumulating state.

Panels dock to the bottom below 1120 px so the trace cannot cover the answer on
a laptop screen.

### 21.7 A question the user could not see

Reported as "it didn't ask me anything, no output". It had asked — the answer
was rendered and then covered.

| ID | Finding |
|---|---|
| **F-57** | **The agent trace auto-opened on top of the clarifying question.** For a clarification the trace is five instant nodes and pure noise, while the one thing the user needed was the question underneath it. The trace panel now CLOSES when ORCA is asking rather than answering, the question renders in its own card, and the input is focused with a worked example as its placeholder (`e.g. "near Kochi" or "9.93N 76.26E"`). A question the user cannot see is not a question. |

Also fixed: `/ui` assets are served `no-store`. A stale `app.js` is
indistinguishable from a bug and cost two debugging rounds.

### 21.8 Self-inflicted

`app.js` was destroyed mid-session by a careless `str.replace()` — Python
replaces EVERY occurrence, and the pattern recurred, producing a 1.5 MB file of
duplicated blocks. The file was untracked, so there was no copy to restore.
Rewritten from scratch with every fix folded in.

The lesson is a process one and worth writing down: **the editing technique used
throughout this project — scripted whole-string replacement — is unsafe on
repeated content.** Commit before large edits to untracked files, and prefer
anchored, single-occurrence replacements.

### 21.9 An answer that contradicted itself

Reading a real answer closely found two defects that every automated check had
passed.

| ID | Finding |
|---|---|
| **F-58** | **A capped SAFETY verdict contradicted its own headline.** When the cap raises the verdict, `limiting_factor` correctly becomes `official_warning_status` — but the DRIVERS still carried `contribution="limiting"` on wave height from before the cap. The card marked wave height as governing while the headline said the missing warning check did. Only one can be true, and it is the headline: the governing factor is a check that could not be made, not a value that was measured. No driver is marked limiting when a cap governs. |
| **F-59** | **Boundary containment rendered as "absent".** The interface drew every boolean driver as present/absent, so REGULATORY showed `EEZ — absent`, which reads as *there is no EEZ* rather than *you are outside it*. Those are different claims and the second is the true one. A boolean now reads inside/outside in REGULATORY and present/absent elsewhere. |

Both were invisible to the test suite because each part was individually
correct: the engine's `limiting_factor` was right, the drivers' contributions
were right *before* the cap, and the renderer faithfully drew what it was given.
The defect was in the relationship between them, which only shows when a person
reads the whole answer.

---

## 22. Session 12 — the React rebuild, and the map that drew nothing

The interface was rebuilt on React + TypeScript + Vite (`ui/`, built into
`backend/orca/api/webui/`, served at `/ui/`). The vanilla UI is kept at
`/classic`: it is the reference for behaviour that was verified live, and §21
records nine defects that would otherwise be re-derived.

This session took the rebuild from "ported" to the tier list in
`23_FRONTEND_REBUILD_BRIEF.md` §6 being complete.

### 22.1 The route was drawn. Nothing rendered it.

Reported as "route is not getting drawn". The backend was fine — `/v1/chat`
returned `optimized_route` as a 76-point LineString — and so, it turned out, was
the UI: the `route` source and its layers existed in the style, `fitBounds` had
run, and all 76 vertices projected on-screen. `queryRenderedFeatures` still
returned **zero**, and so did a bright red 8 px test line added by hand.

The tell was in which layers worked. The raster basemap painted; every GeoJSON
source reported `loaded: false` forever, and `map.isStyleLoaded()` never became
true. GeoJSON is parsed in a **web worker**.

| ID | Finding |
|---|---|
| **F-60** | **maplibre-gl v6 resolves its worker path from `import.meta.url` at runtime.** v6 ships the worker as a separate file and, with no explicit URL configured, computes `new URL('./maplibre-gl-worker.mjs', import.meta.url)`. That is correct only when the library is served as loose ESM beside its own siblings. Bundled, `import.meta.url` is the *application* chunk, so the worker resolves to `/ui/assets/maplibre-gl-worker.mjs` — which no build emits, because the specifier is computed inside a conditional and so is invisible to the bundler. It 404s **silently**: no console error, no map error event. Every source parsed in the worker — which is every GeoJSON and vector source — hangs unloaded. |

The failure mode is worth stating plainly because it is the opposite of what it
looks like: the basemap loads, the tiles are pretty, and only the *data*
disappears. Route, EEZ boundaries and position markers all vanished together on
a map that appeared to be working perfectly.

Fixed in `ui/src/lib/maplibre-worker.ts` — `setWorkerUrl()` fed from a
`?worker&url` import, so Vite bundles the worker (with the ~500 KB
`maplibre-gl-shared.mjs` a worker cannot share with the main thread) and emits a
real URL — plus `worker: { format: 'es' }`, since maplibre constructs the worker
with `{type: 'module'}`. `config.WORKER_URL` takes precedence over the derived
path, so setting it before the first `Map` is all that is needed.

Two smaller defects fell out of reading the same file:

| ID | Finding |
|---|---|
| **F-61** | **The route effect removed the marker LAYERS and the marker effect never rebuilt them.** The marker effect only checked for the marker *source*, which still existed, so it called `setData` on a source that had no layers left. After any route was drawn the origin and destination markers were gone for good. Layer existence is now checked separately from source existence. |
| **F-62** | **`style.load` and the readiness poll could both complete**, constructing a second `ParticleLayer` over the first and leaking the first one's `move` listener. Guarded with a `ready` flag. |

Once it rendered, the route was still nearly invisible — 2.6 px of pale blue on
a bright basemap. It now has a dark casing under a tinted corridor (§22.3).

### 22.2 Two things the projection could not say

Both of the brief's highest-value items were blocked on the same thing: the API
knew something it never sent.

**D-45 · A driver carries the axis it was judged against.**
`Driver.bands` and `higher_is_worse` now travel with every numeric driver,
straight from the threshold set that decided the band. Before this the interface
drew four equal-width bands with the pin at its band's centre — honest, because
inventing an axis would be inventing a fact, but unable to distinguish a wave
height at the top of *favourable* from one about to leave it. A boolean has no
axis and gets no edges; the equal-width fallback stays for that case, marked `≈`
so it cannot be mistaken for a measured scale.

**D-46 · The temporal strip is built from provenance, not from evidence.**
`temporal_alignment` reports every value that was RETRIEVED, its own validity
window, its true age, whether it was used, and if not, why not. Building it from
evidence would have been easier and would have made the panel useless: evidence
is the list of survivors, so a strip drawn from it can never show a rejection —
which is the one thing it exists to show.

Live, near Kochi, it says exactly what §6 of the brief asked for:

```
sst_anomaly        INCOIS ERDDAP   14.9 yr old   not used — too old for this window
maritime_boundary  MarineRegions   ×4  3.7 yr old  used
chlorophyll_a      CMEMS            2.3 d old    used
chlorophyll_ratio… CMEMS            2.3 d old    used · derived by ratio_to_local_median
sst                CMEMS             32 h old    not used — too old for this window
current_speed      CMEMS           16 h ahead    used · derived · +16.1 h lead
```

Two details in there are load-bearing. A **derived value's inputs count as
used** — the raw chlorophyll is the reason the ratio exists, and marking it
unused would have put the strip's central distinction on the wrong row; the
lineage is walked through `Derivation.inputs`, with a cycle guard. And the
exclusion reason is joined across the **factor/parameter name gap**
(`sst_anomaly_abs` is judged from `sst_anomaly`), because an exact join silently
dropped precisely the rows whose refusal needed explaining.

A 2011 observation beside a forecast for tomorrow spans fifteen years, on which
every current source collapses to a single pixel. The axis is therefore drawn
over the recent span and anything older is pinned to the left edge, hatched, and
labelled with its true age — never rescaled to look as though it were in range.

### 22.3 The rest of the tier list

* **Chlorophyll local-median ring** (T1 #3) — marching squares in `lib/geo.ts`,
  contoured at the field's own median. FISHING judges the *ratio* to the local
  median, so the ring is the comparison the verdict actually made. A cell with
  any masked corner is skipped: no contour is traced through data that was never
  collected.
* **Route corridor** (T2 #8) — wave height sampled per segment and coloured on
  the small-craft band edges. A segment over a hole stays grey and the legend
  says so. The wave fetch happens *after* the route is drawn and can never gate
  it. Kochi→Chennai: 39 of 75 segments tinted, 0.41–1.89 m.
* **Geofence range rings** (T2 #9) — geodesic rings per alert, coloured by
  severity, so "0.8 km from the EEZ" and the boundary line are finally in the
  same frame.
* **Provenance as a chain** (T2 #6) — L1 source → L2 derivation → L3 value. A
  value with no derivation gets an explicit "as published" L2 rather than a
  blank step, which would imply a computation happened.
* **Source constellation** (T2 #7) — capabilities grouped by the domain they
  serve. An unbound one stays visible and dashed with its reason; dropping it
  would make the map of what ORCA can do look complete.
* **Disagreement panel** (T2 #10) — both vocabularies share one severity ladder
  and a spread of two bands or more raises a panel. `UNKNOWN` and
  `INSUFFICIENT_EVIDENCE` are deliberately **off** the ladder: not knowing is a
  gap, not a position that can disagree with one.
* **Freshness dots and confidence** (T3) — age is carried by the dot's ring as
  well as its hue, never by colour alone, and the text is always present too.
  Confidence softens the card edge and the verdict word; the numbers stay
  exactly as readable.

### 22.4 Three ways the port had gone quiet

Each was individually small and each broke a §4 rule the backend works hard to
uphold.

| ID | Finding |
|---|---|
| **F-63** | **`dataset_version` and `advisory only` were rendered only when an alert carried a distance.** An `inside` alert has no distance — so the one alert that most needs to say which snapshot it came from was the one that did not. |
| **F-64** | **Evidence was truncated at twelve with no indication.** A silent cap reads as "that was all the evidence there was". It now says how many more there are. |
| **F-65** | **`plan.unavailable`, `not_evaluated` and `resolution_notes` were fetched and never rendered.** §4 calls the first two first-class content. The fishing query plans four capabilities it cannot fill — thermal fronts, cyclone distance, lightning, official warnings — and the answer said nothing about them. Now: a *Planned for, not available* section, a collapsible *Not evaluated* list, and the resolution notes under the headline, so how the question was read is visible. |

Also: the evidence provenance id was a `<span>` with an `onClick`, which no
keyboard or screen reader could reach. It is a `<button>`.

### 22.5 Not fixed, and why

`ui/` had never been installed in this checkout, and the committed bundle turned
out to be **stale relative to `ui/src/`** — it still contained a single `here`
marker layer where the source has had `here-origin`/`here-dest` for some time.
A committed build artefact that no one can reproduce is a trap; there is no CI
step that rebuilds it. Left as-is, but worth a decision.

The install list in §9 and in the README is also incomplete: it omits `fastapi`
and `uvicorn`, so the server the frontend brief tells you to start cannot be
started by following the documented steps. There is no pinned requirements file
at all.

### 22.6 A pre-existing bug this session did not touch

Ten tests in `tests/graph/test_graph_flow.py` and one in `tests/api/` fail on
`main`, and have nothing to do with the interface. They fail because location
resolution is broken for a very common phrasing:

```
is it safe to go out near Kochi tomorrow morning?   -> clarification_needed: location
is it good for fishing near Kochi tomorrow morning? -> near Kochi
```

`_route_endpoints` matches a bare `\bto\s+(.{2,40}?)$` as a route destination,
so the infinitive in "safe **to** go out" makes `kochi` the destination; it is
then excluded from origin matching and nothing resolves. Every query containing
`to` before a place name is affected — "is it safe to sail near Chennai", "do I
need to worry about waves near Mumbai" — including the first item on the brief's
own verification checklist.

The fix belongs in intent parsing, not in the interface, and changing route
detection has real blast radius, so it was left alone and is recorded here.

---

## 23. Session 13 — the graph as a graph, and a checklist that found two bugs

Two things: the last open item on the tier list, and then actually running
`23_FRONTEND_REBUILD_BRIEF.md` §8 end to end. The checklist was the more
valuable half — it found two defects that every other check had passed.

### 23.1 The agent trace as the graph it is

The timeline shows every node in completion order, which is the right shape for
reading WHAT happened. What it cannot show is that the run has a **shape**: a
plan that fans out across seven tools at once, a validation gate that can send
the run backwards, a per-domain assessment fan-out that is deliberately never
merged. That shape is the thing a dashboard cannot produce, and it was being
rendered as a flat list.

`TraceGraph.tsx` draws the topology from `graph/build.py` as a fixed skeleton
and lights the run over it. Two consequences are the whole point:

* **A node that did not run stays visible, dim.** The path not taken is
  information. `clarify` dark means ORCA did not need to ask; `replan` dark
  means the first plan was sufficient; `human_review` dark means the answer was
  auto-released. Hiding them would make every run look like the only path the
  graph has.
* **The fan-outs are drawn as fan-outs.** Seven parallel tools read as seven
  parallel tools rather than seven consecutive lines — which is what F-56 was
  about in the first place, and what a vertical list structurally cannot show.

The skeleton is hand-maintained against `build.py`. That is a real maintenance
cost and worth stating: a *wrong* picture of the graph would be worse than no
picture, so the edges drawn there are exactly the edges compiled here. The
timeline is kept alongside it, because the graph has no room for per-node codes
and timings; selecting a node reveals them.

**D-47 · Both views, and the graph leads.** They answer different questions —
"what is the shape of this run" and "what happened, in order" — and neither
subsumes the other.

### 23.2 Two bugs the checklist found

Every automated check passed and both of these were still broken, because both
are about a *relationship* between parts that are individually correct.

| ID | Finding |
|---|---|
| **F-66** | **The clarifying question was visible, but the cursor was not in the box.** F-57 fixed this once; the React port reintroduced it in a subtler form. The textarea is `disabled` while streaming, and the `result` event arrives *while `isStreaming` is still true* — so `focus()` was called on a disabled element, did nothing, and nothing re-focused it when the stream ended a moment later. Focus now waits for the stream to end. The bug is invisible to any test that asserts on the DOM rather than on `document.activeElement`. |
| **F-67** | **A Malayalam question was answered in English on screen, while its Malayalam answer sat unused in the response.** `recommendation.headline` is a short English summary; `recommendation.narrative` is the COMPOSED answer and the only field written in the user's language. The interface rendered the headline and discarded the narrative. Language detection, the `ML` badge, the lexicons and the backend composition were all working perfectly — the last step threw the result away. |

F-67 is worth dwelling on because of what it implies. The verdict cards' domain
names, factor names and band labels are all English. The narrative is therefore
not a nicety for a non-English reader; it is the *entire* readable answer. So it
now leads — the headline is its first line — and the rest is kept: collapsed in
English, where the cards below say the same thing, and open otherwise, where
they do not.

### 23.3 §8, run properly

All ten items pass. Three are worth recording because of *how* they were
verified rather than that they passed:

**Three turns, no accumulation.** Turn 1 near Kochi produced three cards
(SAFETY, FISHING_SUITABILITY, REGULATORY), 3 alerts, 9 evidence. Turn 2 "is it
safe there tomorrow morning?" produced **SAFETY only**. Turn 3 "am I inside the
EEZ?" produced **REGULATORY only**. Location carried through all three and was
never re-asked. D-44 holds under a real conversation.

A second run of the same test found something better than a pass: asking "is it
safe?" after a boundary check returned `waiting on time_window` — the session
had carried the location but the boundary question established no window, and a
safety question needs one. The clarification turn showed **zero** verdict cards
rather than the previous turn's stale one, which is the accumulation property
demonstrated at the moment it would be most tempting to get wrong.

**Tile hosts blocked.** Verified for real, by pointing all three basemap hosts
at an unroutable address and rebuilding, rather than by reasoning about it. On a
completely blank map the answer, gauges, alerts, evidence, temporal strip, the
agent graph, the EEZ boundary lines and the position marker all rendered. The
GeoJSON layers are worth calling out: they do not depend on tiles, so "no
basemap" is not "no map" — the boundaries are still there.

**Coverage near Kochi.** 55 %, 5 207 cells masked and drawn as gaps.

### 23.4 Still open

Unchanged from §22.6 and §22.5: the `to`-infinitive location bug still fails ten
graph tests and still breaks "is it safe to go out near Kochi"; the committed
`webui/` bundle is still a build artefact nothing reproduces in CI; and there is
still no pinned requirements file.

---

## 24. Session 14 — the route that was never steered

A verification pass asked four questions. Three came back clean; the fourth found
the most serious defect in the project so far, and it had been passing every test
in the suite.

### 24.1 What the audit found

**Coherence — passes.** Eleven cross-checks over one run and its provenance
chain: every `evidence.provenance_id` resolves, every `driver.evidence_id`
resolves, every driver's number equals its evidence's number exactly, every
provenance record has a temporal-strip row, a capped verdict has no driver
marked limiting and its `limiting_factor` IS the cap, and every alert carries a
`dataset_version` and `advisory_only`. One imprecision worth recording:
`source_id` can be a source GROUP (`S-01..S-04`) rather than the dataset that
actually served the value, so the audit trail cannot answer "which one".

**Multi-run — passes.** Distinct `run_id` per turn; assessments 3 → 1 → 1 and
alerts 3 → 0 → 3 across three turns rather than accumulating; a second thread
resolved "near Chennai" without leaking into the first, which stayed "near
Kochi". D-44 and F-55 hold under a real conversation.

**Out-of-scope — no fabrication, but the wrong guard.** `hi`, `what is c
programming`, `write me a poem about dogs` and a prompt-injection attempt all
produced **zero assessments and zero evidence**; a follow-up naming a place asked
for the intent rather than inventing one. Nothing is fabricated. But all four
were answered with "Where are you asking about?", which treats nonsense as a
marine question missing a detail. `classify()` returns
`smalltalk_or_out_of_scope` **only for empty text**; everything else falls to
`unknown`, which routes to `plan`. The `out_of_scope` edge in `build.py` is
therefore **dead code**. Recorded, not yet fixed.

Translation degrades safely: `t()` falls back to the English key, so a missing
term surfaces in English rather than as a wrong claim. Eighteen leaked into a
Malayalam narrative (`distance`, `thermal`, `front`, `seasonal closure`, …).
A coverage gap, not a correctness risk — numbers and INCOIS/IMD stayed intact.

### 24.2 The route was a shortest path, and looked like more

| ID | Finding |
|---|---|
| **F-68** | **No gridded field ever reached the router.** `geo_reason` collected `OceanField` instances out of `tool_results` — but retrieval returns POINT values, so the list was always empty. Instrumented over one Kochi→Chennai route: **0 fields, and `cost_function` called 5 128 times returning 0.0 every single time.** Every route ORCA has ever drawn was the shortest navigable path. |

The machinery was never broken, only starved. Injecting a synthetic 4 m band
across the corridor moved the route 997 km → 1848 km and pushed its southern
limit from 8.03N to 5.93N. The cost function, the penalties and the A* search
were all correct; nothing fed them.

What makes this the worst defect so far is not the routing. It is that the map
looked right. A distance-only path and a weather-steered path are the **same
picture**, and this interface had just added a corridor tinted by wave height
along it — so the colour invited a reader to believe the route avoided the red
stretches. It did not. The prose never overclaimed (a route answer refuses to
call itself safe), and the layer already carried an honest `note`, but nothing
rendered it: the one place the truth lived was a property nobody saw.

**D-48 · A route declares what steered it.**
`steered_by` now travels with the route layer, empty when nothing steered it,
and both the map legend and a new route card read from it. A route planned on
distance alone says so, in those words, in the answer.

### 24.3 The wiring

Grids are fetched at `geo_reason` for the CORRIDOR — the midpoint plus a radius
covering the whole route with room for the detour a field may force — because
nothing upstream produces them and a field that stops at the straight line goes
blind halfway through a diversion. The provider is injected from the composition
root exactly as `navigable` is, so the graph node still never touches an adapter.

Two things were easy to get wrong here:

* **Row order.** `extract_field_values` indexes from `bbox.min_lat`, so row 0
  must be the SOUTHERNMOST latitude, and sources publish either order.
  `as_ocean_field` normalises and is tested from both directions. A flipped grid
  would apply every penalty to the mirror image of the sea it was measured in —
  worse than no penalty at all, because the route would still look steered.
* **Silent failure.** Every fetch failure is returned as a declared gap, named
  and reasoned, and surfaces in `not_evaluated` as
  `route_steering:<parameter>`. The whole risk being fixed is a distance-only
  line presented as an optimised one; a swallowed exception would reintroduce it.

Live, Kochi→Chennai: both grids fetched (97×97 waves, 17×18 wind), route
996.6 km → **1015.9 km**. The router accepted **19.3 extra km to avoid 80
penalty-km** of rough water, and the corridor's own maximum fell from 1.89 m to
1.78 m. With the adapters removed it degrades to `objective: "shortest navigable
path only"`, the note gains "Sea state was NOT considered", the route card turns
amber, and the corridor legend switches to "shown for information only … this
route did **not** take these conditions into account".

### 24.4 Still open

The `to`-infinitive location bug (§22.6), `out_of_scope` as dead code (§24.1),
the unreproducible committed `webui/` bundle and the missing pinned requirements
file (§22.5).

---

## 25. Session 15 — a greeting is not a marine question

§24.1 recorded that `hi` and `what is c programming` were answered with "Where
are you asking about?". Nothing was fabricated — no verdict, no evidence, and
supplying a location produced "which topic?" rather than an answer — but the
exchange was **untrue about itself**: asking where asserts that the query WAS a
marine question merely missing a detail.

### 25.1 Why the branch was dead

Three things had to be wrong at once, and they were.

| ID | Finding |
|---|---|
| **F-69** | **`classify()` returned `smalltalk_or_out_of_scope` only for EMPTY text.** Everything unmatched fell to `unknown`, which routes to `plan`, which asks for the missing detail. The negative case was never reachable from any real query. |
| **F-70** | **`out_of_scope` was wired straight to `finalize`, which composes nothing.** So even had the branch been reachable, it would have produced an answer with no headline and no recommendation at all. |

Both had to be fixed together: a classification with nowhere to go, and a
destination with nothing to say.

### 25.2 The test is for ABSENCE of signal, and it fails safe

The two errors here are not symmetric. Refusing a question a fisher actually
asked is a failure of the product; asking a clarifying question about nonsense
is merely clumsy. So the rule is deliberately lopsided: a query is out of scope
only when it contains **no marine signal at all**, tested after every intent
keyword has already failed.

`_MARINE_SIGNAL` is therefore wide — marine nouns, vessels, gear, time
expressions, bearings — and a bare position counts on its own. Two further
escapes live in the context node, which is the only place that can see them:

* **a query that names a PLACE.** "near Kochi" and "to Chennai" carry no marine
  noun of their own and are the clarification loop's own replies;
* **a thread with a question outstanding.** An answer is as short and bare as
  the question made it.

**D-49 · A carried location is not marine signal.**
The place test reads the QUERY TEXT only, deliberately ignoring the session and
any client GPS. The first implementation used the resolved location, which
includes the carried one — so `hi` mid-conversation inherited the previous
turn's location, was downgraded to `unknown`, picked up the carried intent and
answered the fishing question again. A remembered location is what makes a
follow-up answerable; it does not make a greeting a question.

For a language whose own lexicon produced no hit, there is no basis to judge, so
it stays `unknown` and asks. Malayalam smalltalk is still answered with a
question — a documented limitation, and the safe direction.

### 25.3 The bug the fix created

| ID | Finding |
|---|---|
| **F-71** | **Out of scope was remembered as the conversation's topic.** `finalize` persists `intent` into `session_context`, and a follow-up inherits it. So a single `hi` mid-thread set the topic to `smalltalk_or_out_of_scope`, and **every later turn inherited it and was refused too** — "what about tomorrow?" after a greeting stopped working. Neither "out of scope" nor "unclassified" is something a follow-up can be ABOUT, so neither is persisted now. |

This one is worth recording because it was invisible in single-shot testing and
only appeared when a real conversation interleaved smalltalk with questions:

```
is it good for fishing near Kochi tomorrow morning?   answered      3 assessments
hi                                                    OUT_OF_SCOPE  0
what about tomorrow?                                  answered      3 assessments
thanks                                                OUT_OF_SCOPE  0
am I inside the EEZ?                                  answered      1
what is c programming                                 OUT_OF_SCOPE  0
is it safe there?                                     answered      1
```

### 25.4 What the answer says, and what the graph shows

The node states what ORCA covers rather than asking where. The interface
suppresses the intent/disposition line (`smalltalk_or_out_of_scope ·
out_of_scope` is internal vocabulary) and the resolution notes — "no location in
the query" would have reintroduced exactly the implication being removed — and
opens the guidance rather than collapsing it, because here the guidance IS the
answer.

The agent graph shows the truth without being told to: `Out of scope` lit,
`Ingest → Resolve intent → Finalise` on the spine, and Plan, Retrieve, Validate,
Assess and Report all dim. No retrieval happened and the picture says so. The
skeleton in `TraceGraph.tsx` was updated in the same change — it is
hand-maintained against `build.py`, and adding a node there without adding it
here would have made the drawing quietly wrong.

An existing test asserted the OLD contract (`hello there` → ask for intent). It
was replaced by two: an unclassifiable but genuinely marine query still asks, and
a query with no marine content is refused. The first is the line that must hold.

455 tests pass, 17 new.

### 25.5 Still open

The `to`-infinitive location bug (§22.6) — ten graph tests, and "is it safe to
go out near Kochi" still fails to resolve a location. The unreproducible
committed `webui/` bundle and the missing pinned requirements file (§22.5).

---

## 26. Session 16 — two wrong-premise bugs, and a green suite

Both bugs here are wrong-PREMISE bugs, which is the worst kind this pipeline can
carry: every number downstream is correctly retrieved, correctly aligned and
correctly assessed — for a place or a time the user did not ask about. Nothing
later in the chain can detect that, because nothing later knows what was asked.

The suite now passes in full for the first time: **478 tests, 0 failures.**

### 26.1 `to` is usually an infinitive

| ID | Finding |
|---|---|
| **F-72** | **The route-destination matcher read every `to` as a preposition.** `_route_endpoints` searched the fragment after `to` with a SUBSTRING test, so "is it safe **to** go out near Kochi" found `kochi` inside a verb phrase and made it the destination. It was then excluded from origin matching and nothing resolved, so the commonest phrasing of the commonest question answered "Where are you asking about?". Every query with `to` before a place name was affected — "is it safe to sail near Chennai", "do I need to worry about waves near Mumbai". |

`to` marks a verb far more often than it marks a destination, and the two
readings put the place in opposite roles: in "route TO Chennai" it is where you
are going, in "safe TO go out near Kochi" it is where you already are.

**D-50 · A destination is NAMED at its slot, not mentioned inside it.**
`_place_at_start` anchors the name to the beginning of the fragment, allowing
only determiners and "port of"-style fillers. A substring search cannot make
that distinction; anchoring can, and it costs nothing — "route from Kochi to the
port of Chennai" still resolves both endpoints.

This one defect accounted for **eight** of the ten long-standing test failures.

### 26.2 The window never moved

Fixing the first exposed the second, which was worse.

| ID | Finding |
|---|---|
| **F-73** | **Every turn after the first reused the first turn's window.** `_resolve_window` read `state["resolved_time_window"]` — its own OUTPUT channel, which a checkpointed thread restores — before parsing the query. So "what about tonight?" was answered for tomorrow morning, and the resolution note said *"window supplied by the caller"* while doing it. A confidently wrong time, with a false account of where it came from. |

The location path had never had this bug because a place named in the query
already beat the carried one. The window is now ordered the same way: this
turn's words, then a per-turn `client_time_window` from the caller, then the
conversation's carried window, then nothing. The carry itself was never wrong —
"how is the fishing?" after "tomorrow morning" should still mean tomorrow
morning — only its priority was.

```
is it safe near Kochi tomorrow morning?   2026-09-04T00:30   (parsed)
what about tonight?                       2026-09-03T12:30   (parsed, moved)
how is the fishing?                       2026-09-03T12:30   (carried, and says so)
```

**D-51 · Caller input never shares a channel with graph output.**
`client_location` already followed this rule; `client_time_window` now does too.
A channel the graph writes and a checkpoint restores cannot also carry what the
caller asked for this turn — the two are indistinguishable once stored, and the
older value silently wins.

### 26.3 What the tests were hiding

Three of the ten failures were the tests' own doing, and each masked something:

* **They seeded `resolved_*` rather than `client_*`.** That is a path no caller
  can take — the API has used `client_location` since the UI work — so the tests
  were exercising a contract that did not exist, and F-73 could never have
  surfaced through them.
* **They hardcoded `2026-09-03` as the analysis window** while asking about
  "tomorrow morning". That passed only until the date arrived, then failed for
  reasons unrelated to the code. The fixtures now derive the window the same way
  the resolver does, so the fixture and the query say the same thing forever.
* **One asserted the pre-fix contract** for `hello there` (§25) and was replaced
  rather than patched.

A test that seeds an output channel is not testing the product; it is testing a
shape the product never sees.

### 26.4 Still open

The committed `webui/` bundle is still a build artefact nothing reproduces in
CI, and there is still no pinned requirements file — the documented install list
omits `fastapi` and `uvicorn` (§22.5). `out_of_scope` for non-English smalltalk
remains a clarifying question rather than a refusal, by design (§25.2).
