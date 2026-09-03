# ORCA — Frontend Rebuild Brief (React)

**Document:** 23 of 30 · **Date:** 2026-09-03
**Status:** The React rebuild is done and the tier work below is complete.
Section 6 records what is built; the vanilla UI is retained at `/classic`.

---

## 1. What exists, and why replace it

`backend/orca/api/static/` — 1,182 lines of vanilla JS served at `/ui/`:

| File | Lines | What it does |
|---|---|---|
| `index.html` | 248 | markup + all CSS |
| `app.js` | 737 | chat, SSE trace, verdict cards, gauges, map layers, provenance |
| `wind.js` | 197 | canvas particle layer for vector fields |

It was written against a same-day deadline: no build step, no framework, plain
DOM. **It works** — every path in §6 is verified live. It is not a good place to
keep building: rendering is string concatenation, there is no component
boundary, and state is module-level `let`.

**Do not treat this as legacy to be ignored.** It encodes nine defects already
found and fixed (§7). Re-deriving those costs a day and some of them are
correctness bugs, not cosmetics.

---

## 2. Read these first, in this order

1. **`02_FRONTEND_DESIGN_SPEC.md`** — the authoritative design. 20 sections:
   information architecture, desktop and mobile layout, conversation, map,
   evidence panel, recommendation cards, warnings, freshness, uncertainty,
   route, PFZ, temporal controls, multilingual, accessibility, role views,
   loading/error/empty states, human review. **This brief does not replace it.**
2. **`IMPLEMENTATION_LOG.md` §21** — what the current UI does and every bug it hit.
3. **`12_RISK_AND_RECOMMENDATION_SPEC.md` §5** — the language rules. The UI can
   violate these as easily as the backend can.
4. The running API: `GET /docs` (FastAPI's generated reference).

---

## 3. The API contract

Base URL is the same origin. Start the server with:

```bash
./.venv/bin/uvicorn backend.orca.api.main:app --port 8000 --reload
```

| Endpoint | Purpose |
|---|---|
| `POST /v1/chat` | `{query, thread_id?, lat?, lon?, language?}` → full answer |
| `POST /v1/chat/stream` | same body → **SSE**: `start`, `node`×N, `result`, `error` |
| `GET /v1/health` | liveness, tools bound, whether an LLM is configured |
| `GET /v1/health/sources` | all 12 capabilities: available, description, unavailable_reason |
| `GET /v1/field/{name}` | `wind`, `current` (vector), `chlorophyll`, `sst`, `waves` (scalar) |
| `GET /v1/fields` | field catalogue |
| `GET /v1/boundaries` | maritime geometry as GeoJSON, bbox-filtered |
| `GET /v1/boundaries/layers` | snapshot layers and version |
| `GET /v1/runs/{thread}` | replay a thread's last state |
| `GET /v1/runs/{thread}/provenance?provenance_id=` | the provenance chain |

### 3.1 The answer projection

`POST /v1/chat` and the SSE `result` event both return:

```
thread_id, language, intent,
resolved_location {lat, lon, label, dest_lat?, dest_lon?},
resolved_time_window {start_time, end_time},
resolution_notes[], clarification_needed,
plan { domains[], required_evidence[], steps[{step_id, tool, necessity}],
       unavailable[{evidence, tool, reason}], reasoning_summary },
assessments[ { domain, verdict, confidence, rationale,
               drivers[{factor, value, unit, band, contribution,
                        bands{band: [low, high]}?, higher_is_worse?}],
               not_evaluated[{factor, reason, detail}],
               missing_required[], verdict_capped_by[], limiting_factor } ],
evidence[{evidence_id, domain, statement, parameter, value, unit,
          value_kind, provenance_id, weight}],
alerts[{kind, boundary_type, severity, distance_km, inside, name,
        dataset_version, advisory_only}],
map_layers[{id, type, name, data /* GeoJSON Feature */}],
temporal_alignment {
  window {start_time, end_time}, generated_at,
  entries[{ provenance_id, tool, parameter, value_kind, source, source_id,
            dataset, valid_time, valid_from, valid_to, reference_time,
            lead_time_h, representativeness, retrieved_at, age_s,
            used, derived_via, evidence_id, domain,
            excluded_reason, excluded_detail }]
},
claims[], not_evaluated[], disposition, recommendation, trace[]
```

`temporal_alignment` is built from **provenance**, not from evidence, so a value
that was retrieved and then refused still has a row. A strip drawn from evidence
alone shows only the survivors and can never show a rejection. A derived value's
inputs are marked `used` too — the raw chlorophyll behind a ratio is the reason
the ratio exists.

### 3.2 SSE shape

```
event: start   data: {"thread_id": "..."}
event: node    data: {node, status, duration_ms, summary, tool?, source?,
                      codes?, fallback_used?}
event: result  data: <the projection above>
event: error   data: {"error": "..."}
```

Events are separated by a blank line. **Every** node event is emitted, including
all parallel `tool_exec` events in one superstep — that fan-out is the point
(F-56).

### 3.3 Field shape

```
field, label, kind: "scalar"|"vector", unit,
lats[], lons[],
values[][]            // scalar; null where masked
u[][], v[][], speed[][]  // vector; null where masked
range {min, max},
cells {total, valid, coverage},
valid_time, source, source_id, dataset, advisory_only
```

---

## 4. Rules the interface inherits

These are not style preferences. Each one exists because breaking it makes ORCA
state something untrue, which is the failure the whole system is built to avoid.

**A hole stays a hole.** A `null` cell is masked — land, cloud, or no
observation. Render it transparent. Drawing it as `0` paints a calm, empty sea
over data that was never collected. Always show `cells.coverage`.

**A layer that fails is absent, not empty.** Say so, with the reason. An empty
map reads as calm water.

**The map never gates the answer.** Render text first, draw the map after, in a
boundary that cannot propagate. Three separate bugs came from violating this
(F-51, F-52, F-53).

**Never invent a scale.** A driver now carries its `bands` — the real edges it
was judged against — so the gauge places the pin at a true position. Where they
are absent (any boolean; anything without a numeric axis) the bands are drawn
equal-width and marked `≈`. Do not substitute plausible-looking numbers: a
made-up axis is a made-up fact.

**Advisory only.** Every boundary, route and PFZ carries `advisory_only: true`
and a `dataset_version`. Show both. The disclaimer is not decoration.

**Never claim safety that was not assessed.** If `verdict_capped_by` is
non-empty the verdict is a *ceiling*, not a measurement. Say so.

**Words matter per domain.** A boolean is containment in REGULATORY
(inside/outside) and presence elsewhere (present/absent). "EEZ absent" is a
different and false claim (F-59).

**Show what was not checked.** `not_evaluated` and `plan.unavailable` are
first-class content, not an error state.

---

## 5. Recommended stack

- **React + TypeScript + Vite.** Type the API projection in §3.1 — most UI bugs
  here were shape mismatches.
- **MapLibre GL** via `react-map-gl`. Keep the map in one component that owns
  its own readiness; do not let it suspend the tree.
- **deck.gl** if you want `TripsLayer` (animated route) and `HeatmapLayer` for
  less custom canvas work. Optional — the existing `wind.js` is framework-free
  and can be lifted as-is into a `useEffect`.
- **TanStack Query** for the REST calls; SSE stays a hand-rolled `fetch` +
  `ReadableStream` reader (see `app.js` `ask()` — the chunk-boundary handling is
  correct and worth copying).
- **Serve the build from FastAPI's existing static mount**, or run Vite
  separately — CORS is already `*`.

**Component boundaries that matter:** `<Conversation>`, `<AgentTrace>`,
`<VerdictCard>`, `<ThresholdGauge>`, `<EvidenceList>`, `<ProvenancePanel>`,
`<MapCanvas>`, `<FieldLayer>`, `<LayerBar>`, `<SourceHealth>`, `<Legend>`.

---

## 6. Component status

Tiers are from the visual plan. **Done** means verified live in a browser.

### Tier 1 — showstoppers

| # | Component | Status | Notes |
|---|---|---|---|
| 1 | Animated wind / current particles | **Done** | `wind.js`: bilinear sampling, speed-coloured trails, respawn on a hole. Both `wind` and `current`. Lift as-is. |
| 2 | Live agent trace | **Done, as a graph** | Two views over the same events. **Graph** draws the topology from `build.py` as a fixed skeleton and lights the run over it: the seven-tool fan-out reads as a fan-out, the three-domain assessment spread as a spread, and a node that did NOT run stays visible and dim — `clarify` dark means ORCA did not need to ask, `replan` dark means the first plan sufficed. Selecting a node gives its codes, source and timing. **Timeline** is the original vertical list, kept because the graph has no room for per-node detail. |
| 3 | Chlorophyll field | **Done** | Heatmap with holes and coverage %. The local-median contour is drawn as a dashed ring (`lib/geo.ts` marching squares), so the ratio the verdict actually used is visible. A cell with any masked corner is skipped rather than contoured through. |

### Tier 2 — over data the API already returns

| # | Component | Status | Notes |
|---|---|---|---|
| 4 | Threshold band gauges | **Done** | `Driver.bands` and `higher_is_worse` now travel with every numeric driver, so segments have their true widths, the pin sits at its real position and each edge is ticked and labelled. A factor with no numeric axis (any boolean) keeps the equal-width fallback, marked `≈` so it is never mistaken for a measured scale. |
| 5 | Temporal alignment strip | **Done** | `temporal_alignment` added to the projection, built from **provenance** rather than evidence so a value that was retrieved and then refused still appears. Each row is one value's own validity against the analysis window, with its true age, whether it was used, and why not. Verified live: 2011 INCOIS SST reads `14.9 yr old — too old for this window`; CMEMS chlorophyll at `2.3 d old` reads used, and so does its raw input, reached through the derivation chain. |
| 6 | Provenance chain | **Done, as a chain** | Drawn as L1 source → L2 derivation → L3 value, in the direction the data travelled, with a connecting rail. A value with no derivation gets an explicit "as published" L2, never a blank step that would imply a computation. |
| 7 | Source health | **Done, as a constellation** | Grouped by the domain each capability serves, with per-domain counts (SAFETY 3/7, FISHING 4/4, REGULATORY 1/1). An unbound capability stays visible, dashed, carrying its reason — dropping it would make the map of what ORCA can do look complete. |
| 8 | Route ribbon | **Done** | Dark casing under a wave-tinted corridor, animated dash on top, fit-to-bounds. Wave height is sampled per segment from `/v1/field/waves`; a segment over a masked cell stays grey and the legend says so rather than inheriting a neighbour's value. **The tint is not a claim about the route:** `steered_by` travels with the layer, and the legend and route card state whether the router was actually steered by those conditions or merely has them drawn on it. A distance-only route says so in those words (F-68, D-48). |
| 9 | Geofence proximity | **Done** | Alert cards plus geodesic range rings on the map, one per alert that carries a distance, coloured by severity. Distance and the map are finally in the same frame. Cards now show `dataset_version` and `advisory only` on **every** alert, not only those with a distance — an `inside` alert had been losing both. |
| 10 | Disagreement panel | **Done** | Divergence is named without merging anything: both vocabularies share one severity ladder, and a spread of two bands or more raises a panel naming each domain, its verdict and its governing factor. `UNKNOWN` and `INSUFFICIENT_EVIDENCE` are deliberately off the ladder — not knowing is a gap, not a position that can disagree. Adjacent bands do not trigger it. |

### Tier 3 — texture

| Component | Status |
|---|---|
| Dark nautical theme | **Done** |
| Bathymetry basemap (Esri Ocean, key-free, fallback chain) | **Done** |
| Animated caustics | **Done** |
| Glassmorphism panels | **Done** |
| Monospace provenance ids | **Done** |
| Responsive / narrow-screen stacking | **Done** |
| Freshness dots decaying with age | **Done** — six levels from `current` to `far outside any useful window`, plus a distinct `forecast` state for a value valid ahead of now. Age is carried by the ring as well as the hue, so the cue never rests on colour alone, and the text is always present too. |
| Confidence as visual uncertainty (blur/opacity rather than a badge) | **Done** — card border and accent strengthen with confidence and the verdict word takes a sub-pixel blur at LOW. Deliberately subtle: the numbers stay exactly as readable, so only the verdict word and the edge carry it. |

### Roughly

**Tier 1 100 %** · **Tier 2 100 %** · **Tier 3 100 %**.

Every item in the tier list is built and verified live. §8 is fully green.

---

## 7. Traps already hit — do not re-derive these

| ID | Trap |
|---|---|
| **F-51** | A remote raster source in the **initial** MapLibre style stalls `style.load` forever if tiles are blocked, so nothing initialises. Start with an empty style; add the basemap after. |
| **F-52** | `map.on('load')` waits for **tiles**, not the style. Poll `isStyleLoaded()`. |
| **F-53** | `addSource` on an unloaded style throws and, uncaught, replaced the entire answer with "Request failed". Isolate map calls. |
| **F-56** | Emitting only the newest `node_event` per superstep collapses a seven-tool parallel fan-out to one line. Emit all. |
| **F-57** | Auto-opening the trace panel covered the clarifying question; the user concluded nothing had been asked. When `clarification_needed` is set, get the trace out of the way, focus the input, and hint the expected answer. |
| **F-58** | A capped verdict's `limiting_factor` is the capping factor, but drivers may still carry a stale `contribution: "limiting"` — fixed backend-side; assert the card and headline agree. |
| **F-59** | Boolean rendering must be domain-aware (§4). |
| — | Serve UI assets `no-store`. A stale bundle is indistinguishable from a bug. |
| — | MapLibre needs a real basemap host: CARTO, Esri and OSM all work without a key; **Mapbox does not** — if you see "API key required", `mapbox-gl` got loaded instead of `maplibre-gl`. |
| **F-60** | **maplibre-gl v6 resolves its worker from `import.meta.url` at runtime.** Bundled, that points at the application chunk, so the worker resolves to `/ui/assets/maplibre-gl-worker.mjs` — a file no build emits, because the specifier is computed and therefore invisible to the bundler. It 404s with **no console error**, and every source parsed in the worker — which is every GeoJSON and vector source — stays `loaded: false` forever. Raster tiles are unaffected, so the basemap paints normally and only the DATA vanishes: no route, no boundaries, no markers, on a map that looks healthy. `isStyleLoaded()` never becomes true. Fixed by `src/lib/maplibre-worker.ts`: `setWorkerUrl()` from a `?worker&url` import, plus `worker: {format: 'es'}` in the Vite config. **Do not remove that import.** |

---

## 8. Verification checklist

All verified in a real browser at 1500×940, against live sources.

- [x] Fishing query → 3 independent verdict cards, gauges, alerts, evidence
      — 3 cards (SAFETY, FISHING_SUITABILITY, REGULATORY), 5 gauges, 3 alerts, 9 evidence
- [x] All seven tools appear individually in the trace
      — as a genuine fan-out: `chlorophyll, currents, maritime_boundaries, pfz,
      sst, wave_conditions, weather`, each with its source, codes and timing
- [x] `plan a route` → visible question, focused input, trace not covering it
      — required a fix: the textarea is disabled while streaming, so the
      existing `focus()` ran on a disabled element and did nothing (F-66)
- [x] Answering it → route drawn, 75 segments, both endpoint markers
- [x] Three-turn conversation carries location and **does not** accumulate verdicts
      — turn 1 three cards, turn 2 SAFETY only, turn 3 REGULATORY only, all
      "near Kochi" and never re-asked
- [x] A Malayalam query answers in Malayalam with numbers and IMD/INCOIS intact
      — required a fix: the localised answer is `recommendation.narrative`, and
      the interface rendered only the always-English `headline` (F-67)
- [x] Chlorophyll layer shows holes; legend reports ~50 % coverage near Kochi
      — 55 %, 5 207 cells masked and drawn as gaps
- [x] With the network to tile hosts blocked, chat and verdicts still work
      — verified by pointing all three basemap hosts at an unroutable address:
      answer, gauges, alerts, evidence, temporal strip, the agent graph, the EEZ
      boundaries and the position marker all render on a blank map
- [x] `verdict_capped_by` present → no driver marked limiting; ceiling stated
- [x] REGULATORY booleans read inside/outside
