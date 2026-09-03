# Recorded upstream fixtures — MarineRegions (S-08)

Captured live on **2026-09-02** from
`https://geo.vliz.be/geoserver/MarineRegions/wfs`, unauthenticated, HTTP 200.

| File | Request |
|---|---|
| `wfs_eez_12nm_pakistan.json` | `GetFeature typeName=MarineRegions:eez_12nm CQL_FILTER=territory1='Pakistan'` |
| `wfs_eez_24nm_bangladesh.json` | `GetFeature typeName=MarineRegions:eez_24nm CQL_FILTER=territory1='Bangladesh'` |
| `capabilities_featuretypes.xml` | the `eez` and `eez_12nm` `<FeatureType>` blocks of `GetCapabilities` |

Two small real national polygons were chosen deliberately: the adapter suite has
to decode geometry the service actually publishes, including its MultiPolygon
nesting and its attribute spelling. A hand-written polygon would make the suite
test a fiction.

**Axis order.** The layers declare `urn:ogc:def:crs:EPSG::4326`, whose authority
axis order is latitude, longitude. A CQL `BBOX(the_geom, 60, -2, 100, 26)` is
read as lat 60..100, lon -2..26 and returns the Arctic, not the Indian Ocean.
The capabilities excerpt records the `DefaultCRS` declaration that explains it.

**Versioning.** The service publishes no separate version field; the release is
stated in the layer title, e.g. *"Exclusive Economic Zones (200 NM)
(v12, world, 2023)"*. That string is the only machine-readable statement of
which geometry a run used, so `capture_boundaries.py` refuses to write a
snapshot when it cannot parse one.
