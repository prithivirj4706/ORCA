# ORCA — web interface

React + TypeScript + Vite. The authoritative design is
`../02_FRONTEND_DESIGN_SPEC.md`; `../23_FRONTEND_REBUILD_BRIEF.md` records what
is built and the traps already hit.

```bash
npm install
npm run dev      # http://localhost:5173, proxies /v1 to :8000
npm run build    # emits into ../backend/orca/api/webui, served at /ui/
npm run lint
```

The backend must be running for either mode:

```bash
../.venv/bin/uvicorn backend.orca.api.main:app --port 8000 --reload
```

`VITE_API_TARGET` overrides the dev proxy target (default `http://localhost:8000`).

## Things that will bite

**The MapLibre worker is bundled explicitly.** `src/lib/maplibre-worker.ts` sets
`setWorkerUrl` from a `?worker&url` import. maplibre-gl v6 otherwise derives the
worker path from `import.meta.url` at runtime, which resolves to a file no build
emits once the library is bundled. The failure is silent and total: raster tiles
keep working, so the basemap paints while every GeoJSON source — route,
boundaries, markers — stays unloaded forever. Do not remove that import.

**The map never gates the answer.** Text renders first, map calls are isolated,
and the initial style carries no remote source (F-51 … F-53). A blocked tile host
must leave the verdict fully readable.

**A hole stays a hole.** `null` is masked data, never zero: scalar fields
rasterise it transparent, the particle layer respawns on it, the route corridor
leaves the segment untinted, and the legend always states coverage.

**Never invent an axis.** Gauges draw the band edges the API returns. Where it
returns none, the bands are drawn equal-width and marked notional rather than
given plausible-looking numbers.
