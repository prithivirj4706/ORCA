# ORCA — Ocean Reasoning & Collaborative Agents

**SIH26176 · ISRO · Software** — an integration and reasoning layer over authoritative
marine information systems (INCOIS, IMD, CMEMS, MarineRegions).

ORCA does **not** replace INCOIS/IMD/ISRO services. It cites them, integrates them,
reasons across them, and says plainly what it does not know.

## Status

Design documents 01–22 are complete (`0*.md`, `1*.md`, `2*.md`).
Implementation is at **Phase 1–6 partial — ~70% of backend logic**.

| Component | State |
|---|---|
| Canonical schema (`schemas/`) | ✅ provenance invariant, error taxonomy, assessment types |
| INCOIS ERDDAP adapter (`adapters/incois_erddap/`) | ✅ live, verified |
| CMEMS adapter (`adapters/cmems/`) | ✅ live — waves, currents, wind, SST, chlorophyll |
| NOAA GFS adapter (`adapters/noaa_gfs/`) | ✅ live — the wind **forecast** source (S-11) |
| INCOIS GeoServer adapter (`adapters/incois_wms/`) | ✅ live — official **PFZ advisory**, vector (S-06) |
| MarineRegions adapter (`adapters/marineregions/`) | ✅ live — versioned EEZ / 12 NM / 24 NM / internal-waters snapshot |
| Cross-source fallback | ✅ time-aware source selection, recorded in provenance |
| Capability tools (`tools/`) | ✅ 9 of 12 bound; 8 return data. Run `scripts/check_sources.py` |
| Sources reached live in one run | ✅ 5 — INCOIS ERDDAP, INCOIS GeoServer, CMEMS, MarineRegions, NOAA |
| Geospatial kernel (`geospatial/`) | ◐ geodesy, temporal alignment, derivations, point-in-polygon |
| Assessment engine (`assessment/`) | ✅ thresholds, sufficiency, verdicts, confidence, synthesis, REGULATORY |
| LLM provider (`llm/`) | ✅ provider-agnostic; **ORCA runs with no model configured** |
| Agents (`agents/`) | ✅ all five — Planner, Discovery, Geospatial, Risk, Reporting |
| LangGraph orchestration (`graph/`) | ✅ end to end, incl. durable human-review interrupt |
| CLI (`cli/`) | ✅ `ask` (agent-planned) and `query` (fixed slice) |
| Other adapters (IMD, INCOIS WMS) | ⬜ not started — credential / network blocked |
| RAG, API, frontend | ⬜ not started |

## Quick start

```bash
python3 -m venv .venv
./.venv/bin/pip install pydantic httpx certifi truststore numcodecs numpy pyyaml \
                        pytest langgraph

./.venv/bin/python -m pytest tests -q             # 289 offline tests, no network, no LLM
./.venv/bin/python -m scripts.capture_boundaries  # boundary snapshot (~35 s, 7.2 MB)
./.venv/bin/python scripts/check_sources.py       # live source audit

# ask a question -- a Planner decides what to retrieve
./.venv/bin/python -m backend.orca.cli.ask "is it good for fishing near Kochi tomorrow morning?"
./.venv/bin/python -m backend.orca.cli.ask "am I inside the Indian EEZ near Kochi?"
./.venv/bin/python -m backend.orca.cli.ask "is there a warning in force right now?"
```

The PLAN block differs between those three. The fishing question plans six tools and
declares five capability gaps; the boundary question plans one; the warning lookup plans
**none** — twelve capabilities exist, its only source needs credentials, and the answer
says so rather than substituting something else.

**No LLM is required.** With `ORCA_LLM_PROVIDER` unset (see `.env.example`) ORCA plans
from deterministic tables and answers from a grounded template. Configuring a model adds
fluency; validators stop it changing a number or a verdict, and the tests assert that.

`orca-ask` accepts `--lat --lon --when --no-trace`; `orca-query`, the fixed vertical
slice retained for comparison, accepts `--lat --lon --label --when`.

`data/boundaries/` is git-ignored, so run `capture_boundaries` once after cloning.
Without it `get_maritime_boundaries` returns `DATASET_UNAVAILABLE` naming the script —
the correct degradation, not a failure.

## What the slice currently does

1. Queries live INCOIS ERDDAP for temperature, salinity, SST and chlorophyll near a point.
2. Normalises every value into the canonical schema with full provenance — source,
   dataset, published unit, validity time, retrieval time, grid resolution, nearest-node
   distance and valid-cell coverage.
3. Filters evidence by **representativeness**: a 10-day subsurface analysis is refused as
   safety evidence, and archive data is refused for a present-day window.
4. Assesses SAFETY and FISHING_SUITABILITY **independently** against versioned threshold
   sets, applying worst-factor-governs (never averaging), and refuses to issue a verdict
   when a required input is missing.
5. Falls back across sources when the primary cannot serve the requested time,
   choosing the source closest to that time and recording the switch.
6. Evaluates REGULATORY by point-in-polygon against **versioned** boundary geometry,
   reporting the dataset version, the distance to the boundary, and every boundary type
   it could **not** check — an EEZ polygon is never used as a stand-in for a restricted
   zone or a fishing regulation.
7. Synthesises a headline that names the limiting factor across domains.

With current chlorophyll available it produces a live `FISHING_SUITABILITY = FAVOURABLE`
verdict; when CMEMS denies that product it falls back to the INCOIS archive, flags it
stale and returns `INSUFFICIENT_EVIDENCE` instead of guessing. `SAFETY` returns
`INSUFFICIENT_EVIDENCE` either way — an official warning has no substitute, and none is
reachable without IMD credentials.

It also produces a live `REGULATORY` status. A position 60 km inside the Sri Lankan EEZ
returns `RESTRICTED` — operating there needs that state's authorisation — and says so
even though safety cannot be assessed, because a boundary holds whatever the weather
does. Beyond every EEZ the answer is `UNKNOWN`, not `PERMITTED`: high-seas fishery
regulation has no configured source, and not knowing is not permission.

An official marine warning, when present, overrides ORCA's own thresholds — ORCA conveys
and contextualises the authority rather than competing with it.

```bash
# refuses: no wave/wind/warning source for a present-day window
./.venv/bin/python -m backend.orca.cli.query

# produces a real verdict: archive date with genuine SST coverage
./.venv/bin/python -m backend.orca.cli.query --when 2011-06-15T00:30:00

# REGULATORY = RESTRICTED, stated despite the safety refusal
./.venv/bin/python -m backend.orca.cli.query --lat 7.00 --lon 79.30 --label "west of Colombo"
```

## Verified data reality (2026-09-02)

**INCOIS ERDDAP** — access confirmed, but most datasets are historical archives. Only
the Argo analysis products (`incois_argo_10d_VAM`, ends 2026-07-30) carry recent data,
and they are 10-day subsurface means. There is **no current chlorophyll or SST** here.

**CMEMS** — the ARCO (Zarr) store serves **wave and current forecasts without
credentials**, covering tomorrow. This is the only source able to answer a question about
the future. The observation products (SST, chlorophyll, wind) served unauthenticated
reads initially and later returned `AccessDenied`, so they are **not reliable without
credentials** — obtaining them is a priority. See `03_DATA_SOURCE_MATRIX.md` §15.5.

**MarineRegions** — reachable and unauthenticated all the way to feature geometry.
EEZ v12, territorial seas / contiguous zones / internal waters v4, all 2023 releases.
The service publishes the version only inside the layer title, so the capture parses it
and refuses to write a snapshot it cannot version. Its CQL bounding boxes are read
**latitude first**, which silently returns the wrong hemisphere.

Full results, including the incomplete TLS chain, the dataset with a latitude axis in
array indices, the dataset that vanished mid-session, and the boundary layers that are
bands rather than nested zones:
[`03_DATA_SOURCE_MATRIX.md` §14–16](03_DATA_SOURCE_MATRIX.md).

## Layering rule

```
graph/  →  agents/  →  tools/  →  adapters/  →  external source
```

Only `adapters/` knows URLs, credentials and provider query syntax. Agents see capability
contracts and canonical objects, never a provider API. The seam is `tools/registry.py`:
it hands agents a catalogue of names, argument schemas and declared evidence, while the
callables behind those names are bound by the composition root in `tools/live.py`.

Two further contracts matter as much:

* **`geospatial/` and `assessment/` never import `llm/`.** Every number and every verdict
  is computed deterministically. A model can phrase a conclusion; it cannot reach one.
* **`schemas/` imports nothing from ORCA.** It is a leaf.

`assessment/` also sits outside the retrieval chain and does not import from `adapters/`.
Where the two share policy — boundary geometry versus what containment *means* — each
reads its own section of the same configuration file.

These are asserted by `tests/unit/test_import_boundaries.py` (80 assertions), which also
fails the build on a URL literal appearing anywhere above `adapters/`. An import-linter
configuration should express them in CI; until then the tests are the contract.

## Documents

`01_MASTER_PROJECT_SPEC.md` · `02_FRONTEND_DESIGN_SPEC.md` · `03_DATA_SOURCE_MATRIX.md` ·
`04_ORCA_TOOL_CONTRACTS.md` · `05_CANONICAL_DATA_SCHEMA.md` · `06_AGENT_SPEC.md` ·
`07_LANGGRAPH_WORKFLOW_SPEC.md` · `08_API_SPEC.md` · `09_DATABASE_SPEC.md` ·
`10_RAG_SPEC.md` · `11_GEOSPATIAL_REASONING_SPEC.md` ·
`12_RISK_AND_RECOMMENDATION_SPEC.md` · `13_MULTILINGUAL_AND_ALERTING_SPEC.md` ·
`14_SECURITY_PRIVACY_AND_GOVERNANCE.md` · `15_EVALUATION_AND_TESTING_SPEC.md` ·
`16_DEMO_AND_SIH_PRESENTATION_SPEC.md` · `17_IMPLEMENTATION_ROADMAP.md` ·
`18_REPOSITORY_STRUCTURE.md` · `19_ENVIRONMENT_AND_CONFIGURATION_SPEC.md` ·
`20_OBSERVABILITY_AND_AUDIT_SPEC.md` · `21_RISK_REGISTER.md` · `22_MVP_SCOPE.md`

Not yet written: 23–30 (diagrams, ADRs, gap register, judge Q&A, traceability, glossary,
quickstart, definition of done).

**ORCA output is not an official advisory. Follow IMD and INCOIS bulletins.**
