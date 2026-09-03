# Recorded upstream fixtures — INCOIS GeoServer / PFZ (S-06)

Captured live on **2026-09-03** from `https://incois.gov.in/geoserver/wms`,
unauthenticated, HTTP 200.

| File | Request |
|---|---|
| `pfzlines_hit.json` | `GetFeatureInfo PFZ_Automation:pfzlines` at 12.5 N, 72.0 E, BUFFER 60 px |
| `pfzlines_empty_kochi.json` | the same at 9.93 N, 76.26 E — outside the current issue |
| `sector_hit.json` | `GetFeatureInfo PFZ_Sectors:sector_new` at 14.5 N, 81.0 E |
| `capabilities_pfzlines.xml` | the `<Layer>` block for the PFZ lines layer |

**This closes the verification `03_DATA_SOURCE_MATRIX.md` §S-06 left open.** The
audit recorded S-06 as PENDING because the original test network could not
resolve `services.incois.gov.in`. That host still does not resolve; the working
endpoint is `incois.gov.in/geoserver`, which advertises 342 layers.

**PFZ is vector, not raster.** The design prepared a `RASTER_ONLY` branch for
this source. It is not needed: **WFS is 403 Forbidden**, but WMS
`GetFeatureInfo` is enabled and returns real GeoJSON geometry. A spatial search
is therefore expressed as a `GetFeatureInfo` with a bbox and a pixel `BUFFER`,
which is the search radius.

**The advisory is dated, and the layer has no time dimension.** The server
serves whatever issue is current. The issue date is published as
`Year` + `Julian_day` (e.g. `2026` + `245` = 2 September 2026), so it must be
converted before it can be compared with anything. `pfzlines_hit.json` pins that
attribute spelling.

**The empty fixture is not a failure case.** At Kochi the current issue has no
advisory, and the layer's own extent starts at 11.64 N. "No advisory near you"
and "we did not look there" are different answers, and both fixtures exist so
the suite can tell them apart.
