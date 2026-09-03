# ORCA — Data Source Matrix (Authoritative Source Registry)

**Document:** 03 of 30 · **Version:** 1.0 · **Date:** 2026-09-02
**Supersedes:** `DATA_SOURCE_MATRIX.md` (v0.1)
**Status:** Registry baseline — verification status is as of the 2026-09-02 audit

---

## 1. Purpose and Rules of Evidence

This document is the single authoritative registry of external data sources ORCA may
use. Everything else in the documentation set (tool contracts, adapters, roadmap, risk
register) refers back to this file for source status.

Three rules govern every entry:

1. **A source being a government portal does not imply programmatic accessibility.**
   Access class and verification status are recorded separately.
2. **Absence of evidence is not evidence of absence.** Where a machine-readable interface
   was not found, the entry says *"no publicly documented machine-readable interface was
   identified during this audit"* — never *"does not exist"*.
3. **A network failure on the test machine is not a source failure.** Where local
   verification was blocked by network restrictions, the status is
   `PENDING VERIFICATION`, not `SOURCE_UNAVAILABLE`.

---

## 2. Status Vocabulary

| Status | Meaning | Evidence required |
|---|---|---|
| `VERIFIED` | Independently tested from this project; result recorded | Live request with observed response |
| `CONFIRMED` | Established by audit and provider documentation; access mechanism understood | Documented interface + reachability |
| `AUTH REQUIRED` | Reachable, but registration/credentials are required for the data ORCA needs | Observed auth challenge or documented requirement |
| `PENDING VERIFICATION` | Capability identified; independent test not yet possible | Audit record + reason verification was deferred |
| `NO MACHINE INTERFACE ESTABLISHED` | Human-facing service; no clean programmatic interface established in this audit | Search record |
| `PROPOSED` | Candidate source, not yet examined in depth | — |
| `FUTURE` | Out of current scope | — |

## 3. Access Class

| Class | Meaning |
|---|---|
| A | Direct programmatic access (open API / OGC / ERDDAP) |
| B | Programmatic access conditional on registration or authentication |
| C | File/bulk download; unsuitable as a live per-query API |
| D | Human-facing portal; no reliable programmatic interface identified |
| E | Access mechanism unclear; requires verification |
| F | Not realistically usable as an ORCA runtime dependency |

## 4. Role

| Role | Meaning |
|---|---|
| `PRIMARY` | First-choice source for a capability |
| `FALLBACK` | Used only when the primary fails; always recorded in provenance |
| `SECONDARY` | Used alongside the primary for cross-validation / conflict detection |
| `ENHANCEMENT` | Adds value; must never block the MVP |
| `CONTEXT` | Basemap / reference geometry, not a scientific measurement |

---

## 5. Master Registry — Identity and Access

| ID | Source | Organisation | Capability | Dataset / product | Variable(s) | Access method | Auth |
|---|---|---|---|---|---|---|---|
| S-01 | INCOIS ERDDAP | INCOIS (MoES) | Ocean data retrieval | `incois_oceansat2_datasets` | CHL, KD490, TSM | ERDDAP griddap/tableDAP (CSV/JSON/NetCDF) | None observed for listed datasets |
| S-02 | INCOIS ERDDAP | INCOIS (MoES) | SST + anomaly | `NOAA_AVHRR_AMSR_datasets` | SST, SST anomaly | ERDDAP griddap | None observed |
| S-03 | INCOIS ERDDAP | INCOIS (MoES) | Subsurface / analysis fields | `incois_argo_10day_McCreary`, `incois_argo_10d_VAM`, `incois_argo_mnt_McCreary`, `incois_argo_mnt_VAM` | T/S analysis fields (per dataset metadata) | ERDDAP griddap/tableDAP | None observed |
| S-04 | INCOIS ERDDAP | INCOIS (MoES) | Surface wind | `ascat_daily_datasets` | wind fields | ERDDAP griddap | None observed |
| S-05 | IMD API | IMD (MoES) | Weather, marine warnings, cyclone, lightning | Provider-defined endpoints | wind, precip, temp, warnings, cyclone track/cone, lightning | REST JSON | **Required** (403 unauthenticated) |
| S-06 | INCOIS GeoServer / WMS | INCOIS (MoES) | PFZ and ocean map layers | WMS layers incl. PFZ / SST / Chl-related | rendered layers; attributes TBD | OGC WMS (WFS unverified) | Not established |
| S-07 | CMEMS | Copernicus / EU | Wave + current forecasts; SST/Chl fallback | Product/dataset per variable | Hs, Tp, swell, uo, vo, SST, CHL | Programmatic API / subsetting service | **Required** |
| S-08 | MarineRegions | VLIZ | Maritime boundaries / EEZ | EEZ and related boundary products | polygon geometry | Web service / download (WFS per product) | None observed |
| S-09 | MOSDAC | ISRO / SAC | Indian EO ocean products | OceanSat / satellite SST / scatterometer products | ocean colour, SST, wind | Portal / download tooling | **Required** |
| S-10 | INCOIS OSF / LAS | INCOIS (MoES) | Ocean state forecast | OSF products | Hs, Tp, swell, currents | Human-facing; LAS/OPeNDAP not established | Unclear |
| S-11 | NOAA | NOAA / US | Weather + wave fallback | e.g. global wave/atmos products | wind, Hs, Tp | Public services | Varies |
| S-12 | Argo GDAC | Argo programme | Profile observations fallback | Global Argo profiles | T, S, depth | FTP/HTTP index + NetCDF | None (typical) |
| S-13 | Bhuvan / NRSC | ISRO / NRSC | Basemap + thematic layers | Bhuvan services | imagery/thematic | WMS/WMTS/API | Varies |
| S-14 | data.gov.in | NIC / GoI | Fisheries statistics | Fisheries datasets | catch/production stats | REST | API key |
| S-15 | Protected Planet | UNEP-WCMC / IUCN | Marine protected areas | WDPA marine subset | MPA polygons | API / download | API key |
| S-16 | Global Fishing Watch | GFW | Vessel activity | AIS-derived activity | vessel tracks | API | API key |
| S-17 | NCSCM | MoEFCC | Coastal/ecological reference | Publications / portal | — | Portal | — |
| S-18 | National Hydrographic Office | Indian Navy | Nautical charts / notices | Charts, NtM | — | Download / paid products | — |

---

## 6. Master Registry — Coverage, Operations and Status

| ID | Spatial coverage | Temporal coverage | Resolution | Latency | Role | Status | Priority |
|---|---|---|---|---|---|---|---|
| S-01 | Indian Ocean region (per dataset metadata) | Product archive + recent | Source-defined (record per dataset at adapter build) | Satellite product latency (hours–day) | PRIMARY (chlorophyll) | **VERIFIED** | P0 |
| S-02 | Regional/global per product | Recent + archive | Source-defined | Hours–day | PRIMARY (SST, SST anomaly) | **VERIFIED** | P0 |
| S-03 | Indian Ocean | 10-day / monthly products | Source-defined | Product cadence | PRIMARY (ocean observations/analysis) | **VERIFIED** | P0 |
| S-04 | Regional | Daily | Source-defined | Daily | SECONDARY (wind cross-check) | **VERIFIED** | P1 |
| S-05 | India + Indian seas | Current + multi-day forecast | Bulletin/product-defined | Minutes–hours | PRIMARY (weather, warnings, cyclone, lightning) | **AUTH REQUIRED** | P0 |
| S-06 | Indian EEZ / coastal | Advisory cadence (PFZ typically per-issue) | Rendered layer | Issue cadence | PRIMARY (PFZ) | **PENDING VERIFICATION** | P0 |
| S-07 | Global | Analysis + forecast horizon | Product-defined (e.g. fractional-degree grids) | Hours | PRIMARY (waves, currents); FALLBACK (SST, Chl) | **AUTH REQUIRED** | P0 |
| S-08 | Global | Versioned releases | Vector | Static per release | PRIMARY (EEZ, initial) | **CONFIRMED** (reachable) | P0 |
| S-09 | India + Indian Ocean | Product archive | Product-defined | Acquisition + processing latency | ENHANCEMENT (EO cross-validation) | **AUTH REQUIRED** | P1 |
| S-10 | Indian seas | Forecast horizon | Product-defined | Product cadence | — (need covered by S-07) | **NO MACHINE INTERFACE ESTABLISHED** | P2 |
| S-11 | Global | Forecast | Product-defined | Hours | FALLBACK (wind, waves) | **PROPOSED** | P1 |
| S-12 | Global | Continuous | Profile | Days | FALLBACK (profiles) | **PROPOSED** | P1 |
| S-13 | India | Static/periodic | Varies | — | CONTEXT (basemap) | **PROPOSED** | P1 |
| S-14 | India | Annual/periodic | Administrative unit | — | ENHANCEMENT (historical context) | **PROPOSED** | P2 |
| S-15 | Global | Versioned | Vector | Static per release | ENHANCEMENT (MPA) | **PROPOSED** | P2 |
| S-16 | Global | Near-real-time to delayed | Vessel track | Varies | FUTURE | **FUTURE** | — |
| S-17 | India coastal | — | — | — | Reference reading only | **NO MACHINE INTERFACE ESTABLISHED** | P3 |
| S-18 | Indian waters | Chart edition cycle | — | — | Not a runtime dependency | **NO MACHINE INTERFACE ESTABLISHED** | P3 |

> Resolutions are deliberately recorded as "source-defined" until each adapter records the
> exact grid spacing, time step, units and coordinate conventions from live dataset
> metadata. Writing a specific resolution here without reading it from the dataset would
> be fabrication. Capturing these values is an explicit Phase 1 deliverable
> (`17_IMPLEMENTATION_ROADMAP.md`).

---

## 7. Source Detail Records

### S-01 … S-04 — INCOIS ERDDAP  ·  **VERIFIED**

| Field | Value |
|---|---|
| Organisation | INCOIS (Ministry of Earth Sciences) |
| Capability | Programmatic ocean-data backbone: chlorophyll, SST, SST anomaly, analysis/subsurface fields, surface wind |
| Access method | ERDDAP `griddap` / `tableDAP` REST; CSV, JSON, GeoJSON, NetCDF |
| Authentication | None observed for the listed datasets |
| Role | PRIMARY for `get_chlorophyll`, `get_sst`, `get_ocean_observations`; SECONDARY for `get_currents` (if a suitable active dataset is confirmed) |
| Verification performed | DNS resolution ✔ · TCP connection ✔ · TLS handshake with valid certificate ✔ · HTTP 200 ✔ · catalogue accessible ✔ · active datasets enumerated ✔ |
| Datasets observed | `incois_oceansat2_datasets` (CHL mg/m³, KD490 m⁻¹, TSM mg/L); `NOAA_AVHRR_AMSR_datasets` (SST, SST anomaly); `incois_argo_10day_McCreary`; `incois_argo_10d_VAM`; `incois_argo_mnt_McCreary`; `incois_argo_mnt_VAM`; `ascat_daily_datasets` (wind) |
| Failure modes | ERDDAP 4xx on malformed selector; 5xx under load; empty result for out-of-coverage bbox/time; NaN/fill values inside valid grids; dataset retirement/rename |
| Provenance requirements | `dataset_id`, variable name as published, units as published, grid coordinates, `time` value used, request URL hash, `retrieved_at`, ERDDAP server identity |
| Implementation priority | **P0 — first adapter built** |
| Notes | Exact dataset selection and query construction belong to the adapter, never to agents. Dataset availability must be re-checked at startup and cached with a TTL; the adapter must fail with `DATASET_UNAVAILABLE` (not silently substitute) if a dataset disappears. |

### S-05 — IMD API  ·  **AUTH REQUIRED**

| Field | Value |
|---|---|
| Organisation | India Meteorological Department (MoES) |
| Capability | Marine/coastal weather, fishermen's warnings, port warnings, cyclone track/cone/wind, lightning |
| Access method | REST JSON |
| Authentication | **Required.** Unauthenticated request returned **HTTP 403**. |
| Interpretation | 403 indicates an access/authentication requirement. It is **not** evidence that the service is unavailable, and anonymous access must not be claimed anywhere in this project. |
| Role | PRIMARY for `get_weather`, `get_marine_warnings`, `get_cyclone_track`, `get_lightning` |
| Status | AUTH REQUIRED — registration/credential acquisition is an open blocker (`25_GAP_AND_VALIDATION_REGISTER.md` item C-1) |
| Failure modes | 403 (no/invalid credentials) → `AUTH_REQUIRED`; rate limit → `RATE_LIMITED`; schema drift; bulletin free-text with no machine-readable affected-area geometry → `AMBIGUOUS_AREA`; issue-time/validity ambiguity → `STALE_DATA` risk |
| Provenance requirements | bulletin/advisory identifier, issue time, validity window, issuing office, verbatim text retained for quoting, endpoint identity, `retrieved_at` |
| Implementation priority | **P0** — adapter built against the documented contract; runs in `AUTH_REQUIRED` degradation until credentials exist |
| Notes | Warnings are the one category ORCA quotes verbatim as official. ORCA must never synthesise a "warning" from general weather data. |

### S-06 — INCOIS GeoServer / WMS  ·  **PENDING VERIFICATION**

| Field | Value |
|---|---|
| Organisation | INCOIS (MoES) |
| Capability | PFZ and ocean map layers (PFZ / SST / Chl-related layers identified in the audit) |
| Access method | OGC WMS (`GetCapabilities`, `GetMap`, `GetFeatureInfo`); WFS availability unverified |
| Authentication | Not established |
| Role | PRIMARY for `get_pfz` |
| Verification status | The data audit identified public WMS capabilities and layers. **Independent local verification was deferred** because the test network (college campus) could not resolve `services.incois.gov.in`; `curl` returned *"Could not resolve host"*. |
| Explicit rule | This endpoint **must not be labelled broken**. A DNS failure on a restricted network is a local network condition, not a source outage. |
| Architectural rule | No ORCA capability may depend **exclusively** on this endpoint. `get_pfz` is specified with three explicit branches: vector available, `RASTER_ONLY`, and unavailable. |
| Open question | Whether PFZ **geometry/attributes** are retrievable (WFS / `GetFeatureInfo`) or only rendered imagery (`GetMap`). Until answered, PFZ is treated as potentially raster-only. |
| Failure modes | DNS/network restriction; `GetCapabilities` schema change; layer rename; time dimension absent; `GetFeatureInfo` unsupported → `VECTOR_UNAVAILABLE`; rendered-only → `RASTER_ONLY` |
| Provenance requirements | layer name, WMS version, CRS, `TIME` dimension value used, bbox, image size, request URL hash, `retrieved_at`, and an explicit `representation: raster \| vector` flag |
| Implementation priority | **P0 — verification first**, on an unrestricted network, before adapter completion |

### S-07 — CMEMS  ·  **AUTH REQUIRED**

| Field | Value |
|---|---|
| Organisation | Copernicus Marine Environment Monitoring Service (EU) |
| Capability | Wave and current analysis/forecast; SST and ocean-colour fallback |
| Access method | Programmatic API / subsetting service; NetCDF |
| Authentication | **Required** (account + credentials handled entirely inside the adapter) |
| Verification | Public website reachable. Programmatic access not yet exercised from this project. |
| Role | PRIMARY for `get_wave_conditions`, `get_currents`; FALLBACK for `get_sst`, `get_chlorophyll` |
| Failure modes | Credential failure → `AUTH_REQUIRED`; product/dataset renaming → `DATASET_UNAVAILABLE`; subsetting request too large → `INVALID_BBOX`/`RATE_LIMITED`; forecast cycle not yet published → `NO_DATA`; long download latency |
| Provenance requirements | product ID, dataset ID, variable, forecast cycle/reference time, valid time, grid resolution, `retrieved_at`, external-source flag |
| Implementation priority | **P0** (second adapter after ERDDAP) |
| Notes | CMEMS is an **external (non-Indian-authority)** source and must be badged as such in the UI and provenance. It covers the wave/current need that S-10 (INCOIS OSF/LAS) cannot currently serve programmatically. |

### S-08 — MarineRegions  ·  **CONFIRMED (reachable)**

| Field | Value |
|---|---|
| Organisation | VLIZ (Flanders Marine Institute) |
| Capability | Maritime boundaries, EEZ polygons |
| Access method | Web service / product download (WFS per product) |
| Authentication | None observed |
| Role | PRIMARY (initial) for `get_maritime_boundaries` — **EEZ-type boundaries only** |
| Explicit limitation | MarineRegions is **not** the legal authority for every boundary type. It is an initial, citable geospatial source for advisory context. Territorial sea, contiguous zone, fishing regulation zones, MPAs and restricted/operational areas each require their own authoritative source and are separate registry entries. |
| Mandatory retention | boundary source, product version, jurisdiction, effective date, geometry CRS |
| Failure modes | Version drift between releases; overlapping/disputed boundaries; polygon complexity causing slow point-in-polygon at request time (mitigate by preloading into PostGIS with spatial index) |
| Implementation priority | **P0** — preloaded into PostGIS during Phase 1 |

### S-09 — MOSDAC  ·  **AUTH REQUIRED**

| Field | Value |
|---|---|
| Organisation | ISRO / Space Applications Centre |
| Capability | Indian EO ocean products (OceanSat ocean colour, satellite SST, scatterometer wind) |
| Access method | Portal / download tooling; product files (HDF5, NetCDF) |
| Authentication | **Required.** Registration is needed for relevant programmatic access; anonymous FTP/open-data assumptions must not be treated as guaranteed. |
| Role | ENHANCEMENT — independent Indian EO evidence and cross-validation against S-01/S-02/S-07 |
| Explicit rule | MOSDAC must **not** be a dependency that blocks the MVP. If acquisition latency or file size makes live per-query retrieval unsuitable, representative data is pre-staged and labelled as pre-staged with its retrieval time. |
| Failure modes | Auth failure; large file sizes; acquisition latency exceeding query budget; product-specific parsing complexity |
| Provenance requirements | product name, version, acquisition time, processing level, granule identifier, `retrieved_at`, pre-staged flag |
| Implementation priority | **P1** |
| ISRO relevance | As the problem statement is ISRO-sponsored, MOSDAC integration is strategically valuable and is an explicit Phase-1+ objective — but it is scheduled as an enhancement so that credential delays cannot stall the core system. |

### S-10 — INCOIS Ocean State Forecast / LAS  ·  **NO MACHINE INTERFACE ESTABLISHED**

| Field | Value |
|---|---|
| Capability | Significant wave height, wave period, swell, wind, currents forecasts |
| Audit finding | Human-facing services were identified. A clean public machine-readable interface **was not established** during this audit. |
| Explicit rule | Do not claim a machine-readable OSF/LAS interface exists. Do not scrape human-facing portals or rendered images as a data source. |
| Coverage decision | The corresponding wave/current forecast need is served by **S-07 (CMEMS)** for the MVP. |
| Future action | If a documented OPeNDAP/LAS/data-service interface is confirmed later, it becomes the preferred PRIMARY for waves/currents (Indian authority) with CMEMS demoted to FALLBACK. Tracked in `25_GAP_AND_VALIDATION_REGISTER.md`. |
| Implementation priority | P2 (investigation, not integration) |

### S-11 / S-12 — NOAA, Argo GDAC  ·  **PROPOSED**

External fallbacks for wind/wave (NOAA) and subsurface profiles (Argo GDAC). Adapters are
designed but not scheduled before Phase 1 completion. Both must be badged as external
sources in provenance. Neither may be used silently: any use of a fallback sets
`fallback_used = true` with a reason.

### S-13 … S-18 — Context, enhancement and non-runtime sources

| ID | Decision |
|---|---|
| S-13 Bhuvan/NRSC | CONTEXT basemap and thematic layers. **Not** a primary ocean-data backend unless a specific marine layer is verified. P1. |
| S-14 data.gov.in | ENHANCEMENT for historical fisheries context. P2. |
| S-15 Protected Planet | ENHANCEMENT for MPA geometry in the regulatory domain. P2. Requires licence review. |
| S-16 Global Fishing Watch | FUTURE. Raises additional privacy/policy considerations. |
| S-17 NCSCM | Reference reading; no runtime dependency. |
| S-18 National Hydrographic Office | Charts/NtM are the navigational authority ORCA explicitly does **not** replace; not a runtime dependency. |

---

## 8. Capability → Source Binding

This table is the contract between `04_ORCA_TOOL_CONTRACTS.md` and this registry.

| Capability tool | Primary | Fallback | Secondary / cross-check | MVP-live? |
|---|---|---|---|---|
| `get_weather` | S-05 IMD (AUTH REQUIRED) | S-11 NOAA (PROPOSED) | S-04 ASCAT wind (VERIFIED) | Degraded unless credentials |
| `get_marine_warnings` | S-05 IMD | *(none — a warning has no substitute)* | — | Degraded unless credentials |
| `get_cyclone_track` | S-05 IMD | Configured authoritative alternative (deployment-defined) | — | Degraded unless credentials |
| `get_lightning` | S-05 IMD | *(none configured)* | — | Degraded unless credentials |
| `get_pfz` | S-06 INCOIS WMS (PENDING VERIFICATION) | Configured INCOIS PFZ product path / authorised alternative | — | Conditional |
| `get_sst` | S-02 INCOIS ERDDAP (**VERIFIED**) | S-07 CMEMS, S-11 NOAA | S-09 MOSDAC | **Yes** |
| `get_chlorophyll` | S-01 INCOIS ERDDAP (**VERIFIED**) | S-07 CMEMS | S-09 MOSDAC | **Yes** |
| `get_wave_conditions` | S-07 CMEMS (AUTH REQUIRED) | S-11 NOAA | S-10 if ever established | Yes, with credentials |
| `get_currents` | S-07 CMEMS | S-03 INCOIS ERDDAP (if suitable dataset confirmed) | — | Yes, with credentials |
| `get_ocean_observations` | S-03 INCOIS ERDDAP (**VERIFIED**) | S-12 Argo GDAC, S-07 CMEMS | — | **Yes** |
| `get_maritime_boundaries` | S-08 MarineRegions (**CONFIRMED**) | Preloaded local snapshot | S-15 Protected Planet (MPA, P2) | **Yes** |

**MVP-live guarantee.** Three capability tools (`get_sst`, `get_chlorophyll`,
`get_ocean_observations`) are backed by a **VERIFIED** source with no authentication, and
`get_maritime_boundaries` by a **CONFIRMED** reachable source. This is the floor the MVP
vertical slice is built on; everything else degrades explicitly.

---

## 9. Failure Mode Reference

Canonical error codes are defined in `05_CANONICAL_DATA_SCHEMA.md` §Error model.

| Observed condition | Canonical code | ORCA behaviour |
|---|---|---|
| DNS/TCP/TLS failure, 5xx, timeout | `SOURCE_UNAVAILABLE` | Try fallback; record `fallback_used`; if none, report gap |
| 401/403 | `AUTH_REQUIRED` | No retry; report capability as unavailable with reason |
| Dataset/layer/product missing or renamed | `DATASET_UNAVAILABLE` | Alert operators; do **not** substitute another dataset silently |
| Valid query, empty result | `NO_DATA` | Positive statement of absence with the queried extent/time |
| No warning in force | `NO_ACTIVE_WARNING` | **Successful result**, not a failure |
| No cyclone in force | `NO_ACTIVE_CYCLONE` | **Successful result**, not a failure |
| Data older than the parameter's staleness policy | `STALE_DATA` | Usable only with an explicit label and reduced confidence |
| Partial spatial/temporal overlap with the request | `INSUFFICIENT_COVERAGE` | Report the covered extent; do not extrapolate |
| Only rendered imagery available | `RASTER_ONLY` | Downstream output labelled raster-derived; geometry operations disabled |
| Geometry requested, none obtainable | `VECTOR_UNAVAILABLE` | No manufactured polygons |
| Two authoritative sources materially disagree | `CONFLICTING_SOURCES` | Retain both, surface the conflict, escalate if safety-relevant |
| Free-text warning area not resolvable to geometry | `AMBIGUOUS_AREA` | Quote the warning; do not fabricate a polygon |
| Rate limit hit | `RATE_LIMITED` | Backoff; report if it prevents an answer |

---

## 10. Provenance Requirements (all sources)

Every value entering reasoning carries, at minimum:

```json
{
  "parameter": "sea_surface_temperature",
  "value": 28.6,
  "unit": "degC",
  "value_kind": "observed",
  "location": {"type": "Point", "coordinates": [76.10, 9.85], "crs": "EPSG:4326"},
  "valid_time": "2026-09-02T00:00:00Z",
  "source": "INCOIS ERDDAP",
  "source_id": "S-02",
  "dataset": "NOAA_AVHRR_AMSR_datasets",
  "product_reference": "<published product/dataset reference>",
  "retrieved_at": "2026-09-02T11:04:31Z",
  "spatial_resolution": "<as published by dataset metadata>",
  "temporal_resolution": "<as published by dataset metadata>",
  "quality": {"flag": "nominal", "basis": "source-provided"},
  "external_source": false,
  "fallback_used": false,
  "request_fingerprint": "sha256:<hash of the adapter request>"
}
```

Derived values additionally carry `derivation` (input provenance IDs, method, method
version, parameters). Full schema: `05_CANONICAL_DATA_SCHEMA.md`.

---

## 11. Verification Backlog

Ordered work required to move entries from PENDING/AUTH to VERIFIED. Tracked as
deliverables in `17_IMPLEMENTATION_ROADMAP.md` Phase 1 and as gaps in `25`.

### V-1 · INCOIS ERDDAP (raise VERIFIED → dataset-level verified)
- [ ] For each P0 dataset: record variable names, units, coordinate conventions, grid
      spacing, time step, fill/NaN conventions, valid time range
- [ ] Indian-coast bbox subsetting test
- [ ] Temporal subsetting test
- [ ] CSV / JSON / NetCDF response tests
- [ ] Behaviour on out-of-range bbox and time (confirm it maps to `NO_DATA`, not an error)

### V-2 · INCOIS GeoServer / WMS (on an unrestricted network)
- [ ] `GetCapabilities` retrieval and layer enumeration
- [ ] PFZ / SST / Chl layer `GetMap`
- [ ] `GetFeatureInfo` support and attribute content
- [ ] WFS availability → decides `RASTER_ONLY` vs vector PFZ
- [ ] Time dimension support and advisory cadence

### V-3 · IMD
- [ ] Registration / credential acquisition
- [ ] Endpoint-by-endpoint schema capture (weather, warning, cyclone track, cyclone cone,
      cyclone wind, lightning)
- [ ] Timestamp semantics (issue vs validity), geographic fields, rate limits, error
      bodies

### V-4 · CMEMS
- [ ] Credential acquisition
- [ ] Product/dataset identification for Hs, Tp, swell, surface currents
- [ ] Subsetting request test for an Indian-coast bbox
- [ ] Latency and file-size measurement

### V-5 · MarineRegions
- [ ] Product/version selection, licence check
- [ ] PostGIS load + spatial index + point-in-polygon timing

### V-6 · MOSDAC
- [ ] Account creation and programmatic auth test
- [ ] Exact product identification, representative download, parsing, latency/size record

---

## 12. Explicitly Unverified Areas

The following were **not** established during this audit. No claim is made that they do
not exist:

- Tidal prediction services with a documented machine-readable interface
- Public government AIS / vessel tracking
- Operational harmful-algal-bloom feeds
- Machine-readable ecological sensitivity layers
- Some INCOIS forecast interfaces (see S-10)
- PFZ vector geometry access (see S-06)
- Authoritative machine-readable Indian restricted/operational marine area geometry

Standard phrasing for all of the above:

> "No publicly documented, programmatically accessible source was identified during this
> audit."

---

## 13. Source Governance

- Every adapter records the source's terms-of-use reference in its module docstring.
- Credentials are obtained by the team under their own registration; they are never
  shared, embedded or logged (`14_SECURITY_PRIVACY_AND_GOVERNANCE.md`).
- Attribution for every source used in an answer is rendered in the UI and retained in
  provenance.
- A source-health check runs on a schedule and updates the operational status shown in
  `/v1/health/sources`; this operational status is separate from the audit status in this
  document, which changes only through a documented verification event.

---

## 14. Live Verification Results — 2026-09-02

**Method.** `scripts/capture_datasets.py` against `https://erddap.incois.gov.in/erddap`.
Metadata was **read from the server**, not assumed. Raw capture: `config/datasets.json`.

### 14.1 Correction to §5 and §7

The audit recorded INCOIS ERDDAP as a *"confirmed viable programmatic ocean-data
backbone"*. Live testing confirms **access** but corrects the **currency** of the data:

> **Most INCOIS ERDDAP datasets are historical archives, not near-real-time feeds.**
> Only the Argo analysis products carry data within the last two months, and they are
> 10-day/monthly subsurface analyses — which by the representativeness rule
> (`11_GEOSPATIAL_REASONING_SPEC.md` §8.2) cannot support a next-morning safety or
> suitability verdict.

### 14.2 Dataset currency (17 datasets, days behind 2026-09-02)

| Dataset | Coverage ends | Days behind | Usable | Validator finding |
|---|---|---:|:--:|---|
| `incois_argo_10d_VAM` | 2026-07-30 | 34 | yes | — |
| `incois_argo_10day_McCreary` | 2026-07-30 | 34 | yes | variable 'T_ROIOBS' publishes no units… |
| `incois_argo_mnt_McCreary` | 2026-07-15 | 49 | yes | variable 'T_ROIOBS' publishes no units… |
| `incois_argo_mnt_VAM` | 2026-07-15 | 49 | yes | — |
| `Indian_ARGO_Floats` | 2025-04-23 | 496 | yes | variable 'DATE_CREATION' publishes no units… |
| `ascat_daily_datasets` | 2023-05-21 | 1,199 | yes | — |
| `ascat_mnt_datasets` | 2021-11-01 | 1,765 | yes | — |
| `incois_oceansat2_datasets` | 2020-05-01 | 2,315 | yes | — |
| `incois_valueadded_products_datasets` | 2019-03-30 | 2,713 | yes | variable 'MLD' publishes no units… |
| `incois_tmi_3day_datasets` | 2014-12-31 | 4,263 | yes | — |
| `NOAA_AVHRR_AMSR_datasets` | 2011-10-04 | 5,447 | yes | — |
| `AMSRE_MONTHLY_GLOBAL` | 2011-09-14 | 5,467 | yes | — |
| `incois_argo_sst_weekly` | 2010-12-29 | 5,726 | yes | variable 'ASST' publishes no units… |
| `incois_quickscat_daily_datasets` | 2009-11-21 | 6,128 | yes | — |
| `incois_quickscat_mnt_datasets` | 2009-10-16 | 6,164 | yes | — |
| `IRS_chlorophyll_datasets` | 2006-03-21 | 7,469 | yes | — |
| `AMSR2_3day_Global` | 1915-03-08 | 40,721 | **NO** | latitude axis is not in degrees (geospatial_lat_max=719.0); … |

### 14.3 Newly discovered issues

| ID | Finding | Consequence |
|---|---|---|
| **F-1** | **TLS chain is incomplete.** The server sends only its leaf certificate; the `GlobalSign RSA OV SSL CA 2018` intermediate is absent (`openssl s_client -showcerts` → chain length 1). macOS/Windows succeed via AIA fetching; `certifi` and a plain Linux container **fail**. | Adapter uses the OS trust store (`truststore`) with a bundled intermediate fallback at `config/tls/incois_bundle.pem`. Verification is never disabled. |
| **F-2** | **`NOAA_AVHRR_datasets` publishes latitude as array indices 0–399**, not degrees, despite `units=degrees_north`. It was the only current SST source (to 2026-08-11). | Marked unusable by the metadata validator. Not used. Calibrating it against `NOAA_AVHRR_AMSR_datasets` remains an open task. |
| **F-3** | **That same dataset dropped out of the catalogue mid-session** (19 → 18 datasets; `Currently unknown datasetID`). | Datasets can disappear at runtime. Mapped to `DATASET_UNAVAILABLE`; never substituted. |
| **F-4** | **`AMSR2_3day_Global`** fails the same axis check and reports coverage ending 1915. | Marked unusable automatically. |
| **F-5** | **Raw ERDDAP selectors are rejected by the servlet container** — unencoded `[` `]` produce an HTML 400 before ERDDAP parses the query. | `encode_query()` encodes exactly those characters and preserves selector structure. |
| **F-6** | **No current chlorophyll source exists on this ERDDAP.** `incois_oceansat2_datasets` ends 2020-05-01; `IRS_chlorophyll_datasets` ends 2006-03-21. | `get_chlorophyll` returns archive data flagged `STALE_DATA`. Fishing suitability cannot be assessed from it. |
| **F-7** | **1° Argo grid puts the nearest valid node ~96 km from Kochi**; coastal cells are null. | Reported as `nearest_node_distance_km`; drives confidence reduction. |

### 14.4 Revised capability reality

| Tool | Best available INCOIS ERDDAP source | Currency | MVP behaviour |
|---|---|---|---|
| `get_ocean_observations` | `incois_argo_10d_VAM` (TEMP, SAL) | ends 2026-07-30 | **Live**, flagged `STALE_DATA` for present-day queries |
| `get_sst` | `NOAA_AVHRR_AMSR_datasets` | ends 2011-10-04 | Archive only, `STALE_DATA` |
| `get_chlorophyll` | `incois_oceansat2_datasets` | ends 2020-05-01 | Archive only, `STALE_DATA` |
| `get_weather` (wind) | `ascat_daily_datasets` | ends 2023-05-21 | Archive only; not a substitute for IMD |

**Consequence for `22_MVP_SCOPE.md` §7.** The "guaranteed floor" of four live capabilities
is weaker than recorded: ERDDAP provides *access* reliably, but only
`get_ocean_observations` returns data from the last two months. Obtaining **CMEMS
credentials** (waves, currents, current SST/chlorophyll) is therefore not merely the
highest-value action for the MVP — it is required for any present-day verdict.

**No architectural change is implied.** The pipeline behaved exactly as specified: it
retrieved what exists, labelled staleness and spatial mismatch, and refused to issue a
verdict it could not support.

---

## 15. Live Verification — CMEMS (S-07), 2026-09-02

### 15.1 Correction to §7 (S-07 status)

The audit recorded CMEMS as **AUTH REQUIRED**. Live testing shows that is true of
the subsetting and download services, but **not** of the ARCO (Zarr) object store:

| Endpoint | Unauthenticated result |
|---|---|
| `stac.marine.copernicus.eu/metadata/catalog.stac.json` | **200** — 307 products enumerated |
| ARCO store `.zmetadata` | **200** |
| ARCO data chunk `VHM0/0.0.0` | **200**, 521,648 bytes |
| `s3.waw3-1.cloudferro.com/mdl-arco-time-001` (bucket root) | 403 — listing denied, object reads permitted |

Initial reading (revised — see §15.5): *CONFIRMED (ARCO store, no credentials
observed)*. **That correction was too strong and is superseded below.** Credentials are
supported by the adapter and used when configured.

### 15.2 Datasets bound (ids and variables read from the public STAC catalogue)

| Capability | Dataset | Variables | Grid | Cadence | Coverage |
|---|---|---|---|---|---|
| `get_wave_conditions` | `cmems_mod_glo_wav_anfc_0.083deg_PT3H-i_202411` | VHM0, VTPK, VMDR, VHM0_SW1, VTM01_SW1, VCMX | 1/12° | PT3H | 2022-11-01 → **2026-09-12** |
| `get_currents` | `cmems_mod_glo_phy_anfc_merged-uv_PT1H-i_202211` | utotal, vtotal, uo, vo | 1/12° | PT1H | 2020-11-01 → **2026-09-11** |
| `get_weather` (wind) | `cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H_202207` | eastward_wind, northward_wind | 0.125° | PT1H | 2020-07-01 → 2026-09-01 |

**The wave and current datasets extend past today** — they are analysis *and forecast*
products. This is the first source in the project able to answer a question about
tomorrow.

**The wind dataset does not.** It is an L4 near-real-time *observation* product with no
forecast horizon, so a query for tomorrow correctly yields `INSUFFICIENT_COVERAGE`. A
wind forecast still requires IMD or another NWP source.

### 15.3 Findings

| ID | Finding | Consequence |
|---|---|---|
| **F-8** | Coastal cells are land-masked in the wave model. Kochi (9.93 N, 76.26 E) returns no value. | Adapter searches outward for the nearest valid ocean cell within a configurable radius (default 60 km) and reports the distance. At Kochi the nearest valid cell is 10.3 km offshore. |
| **F-9** | VHM0 is stored as `int16` with `scale_factor = 0.01`. | Reading the raw integer would be wrong by two orders of magnitude. Scale and offset are read from the store and applied; a regression test asserts it. |
| **F-10** | Zarr omits chunks that are entirely fill. | A missing chunk reads as *no data*, never as `0.0` — which would present as a calm sea. Asserted by test. |
| **F-11** | The store publishes no scalar wind speed, only components. | `wind_speed` and `wind_direction` are **derived** by the geospatial kernel with a recorded method, version and input provenance ids — not by the adapter. |

### 15.4 Consequence for `22_MVP_SCOPE.md`

§7 recorded `get_wave_conditions` and `get_currents` as "live with credentials". They are
live **without** credentials. The MVP floor is materially stronger than recorded: six
capability tools now return live data, and wave/current forecasts cover the requested
window.

The remaining blocker for a SAFETY verdict is **`official_warning_status`**, which has no
substitute by design — an official warning cannot be synthesised from model fields.

### 15.5 Correction to §15.1 — unauthenticated ARCO access is only partly reliable

Later in the same session, data chunks that had previously returned 200 began returning
`403 AccessDenied`. Re-tested at end of session:

| Product | Bucket | Data chunk | Behaviour across the session |
|---|---|---|---|
| Waves `GLOBAL_ANALYSISFORECAST_WAV_001_027` | `arco-time-015` | **200** | reliable throughout |
| Currents `GLOBAL_ANALYSISFORECAST_PHY_001_024` | `arco-time-015` | **200** | reliable throughout |
| SST `METOFFICE-GLO-SST-L4-NRT-OBS-SST-V2` | `arco-time-045` | **403** | worked initially, later intermittent |
| Chlorophyll `...plankton_nrt_l4-gapfree-multi-4km_P1D` | `arco-time-044` | **403** | worked initially, later denied |
| Wind `cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H` | `arco-time-050` | **403** | worked initially, later denied |

Coordinate arrays and `.zmetadata` remain readable on every bucket; only the **data**
chunks are affected.

| ID | Finding |
|---|---|
| **F-13** | A denied request and a nonexistent key return an **identical** `AccessDenied` body on these buckets (verified against a deliberately nonsensical key). HTTP status and body therefore **cannot distinguish "missing chunk" from "access denied"**. Since Zarr legitimately omits all-fill chunks, this is a genuine ambiguity in the protocol as CMEMS deploys it. |
| **F-14** | The same chunk returned 200 early in the session and 403 later, which points to throttling or a quota on unauthenticated egress rather than a static policy. |

**Engineering consequence.** ORCA treats `404` as an omitted chunk (absent) and `403` as
a **failure**, never as absence. Reading a denial as "no data" would silently drop real
observations and could present a masked sea as a calm one. This costs availability and
buys correctness, which is the right trade for a system that makes safety statements.

**Revised status for S-07:**

> **AUTH REQUIRED** for reliable use. The forecast products (waves, currents) served
> unauthenticated reads consistently and are usable without credentials today; the
> observation products (SST, chlorophyll, wind) are not reliable unauthenticated.
> The audit's original `AUTH REQUIRED` classification was closer to correct than the
> §15.1 correction. **Obtaining CMEMS credentials is now a priority action**, not an
> optional enhancement.

---

## 16. Live Verification — MarineRegions (S-08), 2026-09-02

### 16.1 Access

`https://geo.vliz.be/geoserver/MarineRegions/wfs` answered **unauthenticated**:
`GetCapabilities` HTTP 200 (164 kB, 100+ layers advertised), `DescribeFeatureType`
HTTP 200, `GetFeature` with `outputFormat=application/json` HTTP 200. §5 recorded S-08
as CONFIRMED reachable; that is now verified all the way to feature geometry. No
credentials are configured or required.

Licence: VLIZ Maritime Boundaries Geodatabase, CC-BY 4.0. Attribution is mandatory and
is carried on every provenance record. `14_SECURITY_PRIVACY_AND_GOVERNANCE.md` §"terms"
still calls for a licence review; the attribution string is in place meanwhile.

### 16.2 Layers bound (ids and versions read from the service, not guessed)

| Boundary type | Layer | Version published in the layer title |
|---|---|---|
| EEZ | `MarineRegions:eez` | Exclusive Economic Zones (200 NM) **(v12, world, 2023)** |
| territorial_sea | `MarineRegions:eez_12nm` | Territorial Seas (12 NM) **(v4, world, 2023)** |
| contiguous_zone | `MarineRegions:eez_24nm` | Contiguous Zones (24 NM) **(v4, world, 2023)** |
| internal_waters | `MarineRegions:eez_internal_waters` | Internal Waters **(v4, world, 2023)** |

**No boundary type outside this list has a configured source.** Marine protected areas,
restricted and naval zones, fishing regulation zones and seasonal closures return
`DATASET_UNAVAILABLE` and are listed as not evaluated in every answer.

The captured snapshot (region lat 0–26 N, lon 64–90 E) holds 8 EEZ, 5 territorial-sea,
5 contiguous-zone and 3 internal-waters features — 458,706 vertices, 7.2 MB — read in
11.9 MB over 5 requests.

### 16.3 Findings

| ID | Finding | Consequence |
|---|---|---|
| **F-15** | The layers declare `urn:ogc:def:crs:EPSG::4326`, whose authority axis order is **latitude, longitude**. `BBOX(the_geom,60,-2,100,26)` is read as lat 60–100, lon -2–26 and returns Norway, Svalbard and the Russian Arctic. | Every bbox is emitted lat, lon, lat, lon. The first capture attempt silently returned the wrong hemisphere; the failure mode is a plausible-looking non-empty result, which is the dangerous kind. |
| **F-16** | Features crossing the antimeridian (Kiribati, Hawaii) have envelopes spanning −180…180, so they match *any* bbox query. | Bounding-box prefiltering is done per **ring**, not per feature, and rings that cross the antimeridian are normalised into a continuous 0–360 frame at capture time. Both features are returned by an Indian Ocean bbox and correctly contain nothing. |
| **F-17** | `eez_12nm` and `eez_24nm` are **bands measured from the baseline, not nested discs**. A point 5 NM offshore is inside the territorial sea and *outside* the contiguous zone; a point 20 NM offshore is the reverse. | Boundary types are evaluated independently and combined by "most constraining governs". Treating them as nested would produce a wrong answer in both directions. |
| **F-18** | `eez_internal_waters` publishes **no feature for Sri Lanka or the Maldives**. A point in Sri Lankan waters therefore falls outside every internal-waters polygon in the snapshot. | That is a gap in the source, not a finding about the point. The adapter detects that the layer holds nothing for the governing jurisdiction and downgrades the result to *not evaluated for this jurisdiction*, so a missing polygon can never read as "not in internal waters". |
| **F-19** | The service publishes **no version field**. The release is stated only in the layer title, e.g. *"(v12, world, 2023)"*, and only to year precision. | The title is parsed and recorded; `capture_boundaries.py` **fails** rather than writing a snapshot it cannot version. Effective dates are recorded to year precision, and provenance says so. |
| **F-20** | The full-precision Indonesian EEZ alone is 1.7 M vertices (81 % of an Indian Ocean bbox query). | The default snapshot region stops at 90 E, which excludes it. Positions east of 90 E — including the Andaman and Nicobar waters — are outside the snapshot and return `INSUFFICIENT_COVERAGE` until the capture is re-run with a wider region. |

### 16.4 Consequence for `22_MVP_SCOPE.md`

M-10 ("≥ 3 distinct external sources reached live in one run") is **met**: INCOIS ERDDAP,
CMEMS and MarineRegions all serve a single run. M-25 ("point-in-polygon boundary
evaluation with dataset version and `advisory_only`") is **met**.

REGULATORY is the second fully evidenced domain and the only one that needs no forecast,
no credentials and no network at query time.

---

## 17. Live Verification — INCOIS GeoServer (S-06) and the tide search, 2026-09-03

### 17.1 S-06 is VERIFIED, at a different endpoint

§S-06 recorded **PENDING VERIFICATION** because the test network could not
resolve `services.incois.gov.in`. On an unrestricted network that host *still*
does not resolve — but `incois.gov.in/geoserver` answers `GetCapabilities` with
**342 layers**, including the PFZ set. The verification is closed; the endpoint
in the audit was wrong, not merely unreachable.

| Endpoint | Result |
|---|---|
| `services.incois.gov.in/geoserver` | does not resolve |
| `incois.gov.in/geoserver/wms` `GetCapabilities` | **200**, 446 kB, 342 layers |
| `wfs` `GetCapabilities` and `GetFeature` | **403 Forbidden** |
| `wms` `GetFeatureInfo` (`application/json`) | **200**, real GeoJSON geometry |
| `wms` `GetMap` | **200**, renders |

### 17.2 The open question answered: PFZ is VECTOR

§S-06 asked whether PFZ geometry is retrievable or whether the layer is imagery
only, and the design carried a `RASTER_ONLY` branch for it. **That branch is not
needed.** WFS is closed, but `GetFeatureInfo` returns `MultiLineString` geometry
with attributes, so a spatial search is expressed as a `GetFeatureInfo` with a
bbox and a pixel `BUFFER` acting as the search radius.

Layers bound:

| Purpose | Layer | Extent |
|---|---|---|
| PFZ advisory lines | `PFZ_Automation:pfzlines` | 11.64–23.06 N, 67.15–93.37 E |
| Named advisory sectors | `PFZ_Sectors:sector_new` | 6.35–23.76 N, 67.88–94.78 E |

| ID | Finding |
|---|---|
| **F-30** | The PFZ layer carries **no time dimension**: the server serves whatever issue is current, and the issue date lives in each feature as `Year` + `Julian_day` (`2026` + `245` = 2 Sep 2026). It must be converted before it can be compared, and an undated or old advisory is flagged rather than presented as today's. |
| **F-31** | **The current issue's extent starts at 11.64 N, so Kochi has no PFZ advisory today.** That is a fact about the issue, not about the point. "No advisory near you" and "we did not look there" are returned as different outcomes: `NO_DATA` (checked, none in range) versus `INSUFFICIENT_COVERAGE` (outside the extent). |
| **F-32** | The GeoServer is **intermittently 5xx** (observed several 503s between successful calls). Handled by retry with backoff; a persistent failure degrades to a declared gap. |

### 17.3 Tide — investigated, no reachable source

The problem statement names tide explicitly. Every route was tried on
2026-09-03:

| Candidate | Result |
|---|---|
| UHSLC ERDDAP `global_hourly_fast` | reachable; **Cochin gauge exists at 9.967 N, 76.267 E** — 4 km from the demo point — but `time_coverage_end` is **2026-07-31**, about a month behind |
| CMEMS STAC | the only tide product is `ARCTIC_ANALYSISFORECAST_PHY_TIDE_002_015` — Arctic only |
| CMEMS `SEALEVEL_*` | altimetry anomalies, not tidal height |
| INCOIS `TideGauges:TideGauges` | station **locations** (points), not levels |
| NOAA CO-OPS | US stations only |

| ID | Finding |
|---|---|
| **F-33** | **No reachable source publishes a tide prediction for the Indian coast.** UHSLC is an archive for this purpose, on the same pattern as the INCOIS SST holdings. ORCA will **not** compute its own tide prediction: without published harmonic constituents that would be an authoritative-looking invented number. `get_tides` is therefore a **declared capability with no source**, named in every answer that would have used it. |

**Partial consolation.** Tidal *currents* are already covered: the CMEMS total
surface current product includes the tidal component, so `current_speed`
reflects it. Tidal *height* is what is missing.

**Deviation.** `04_ORCA_TOOL_CONTRACTS.md` specifies eleven P0 tools and does not
include tide, although the problem statement names it. `get_tides` was added to
the catalogue as a twelfth, declared-unavailable capability, so that an answer
about "tide, weather and sea conditions" states what it could not check instead
of quietly answering two thirds of the question.
