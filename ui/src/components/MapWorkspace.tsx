import { useRef, useEffect, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
// Must run before the first Map is constructed: without it every GeoJSON
// source silently never loads. See the file for why.
import '../lib/maplibre-worker';
import { fetchBoundaries, fetchField } from '../api/client';
import { ParticleLayer } from '../lib/particles';
import { rasteriseScalar, type FieldSpec } from '../lib/fields';
import { fieldMedian, isoline, ringPolygon, tintRoute } from '../lib/geo';
import type { Feature, FeatureCollection } from 'geojson';
import type { ORCAAlert, ORCAField, ORCAMapLayer } from '../types/api';

interface Props {
  routeLayer?: ORCAMapLayer | null;
  location?: { lat: number; lon: number; dest_lat?: number; dest_lon?: number } | null;
  field?: ORCAField | null;
  fieldSpec?: FieldSpec | null;
  alerts?: ORCAAlert[];
  onCorridor?: (info: { unit: string | null; min: number; max: number } | null) => void;
}

/* Key-free tile hosts, in preference order. Esri's Ocean Base is first because
 * it is the right basemap for this product: it shades bathymetry, so the sea
 * reads as sea rather than as empty space. */
const BASEMAPS = [
  { id: 'esri-ocean',
    url: 'https://services.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Esri, GEBCO, NOAA · ORCA is not an official advisory',
    opacity: 0.92, saturation: -0.2 },
  { id: 'carto-dark',
    url: 'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap © CARTO · ORCA is not an official advisory',
    opacity: 0.55, saturation: -0.35 },
  { id: 'osm',
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '© OpenStreetMap contributors · ORCA is not an official advisory',
    opacity: 0.45, saturation: -0.6 }
];

/** Wave height (m) -> corridor colour, on the small-craft band edges. */
const WAVE_STOPS: [number, string][] = [
  [0, '#34d399'], [1.5, '#a3e635'], [2.5, '#fbbf24'], [3.5, '#f43f5e']
];

const drop = (map: maplibregl.Map, layers: string[], sources: string[] = []) => {
  for (const id of layers) if (map.getLayer(id)) map.removeLayer(id);
  for (const id of sources) if (map.getSource(id)) map.removeSource(id);
};

export function MapWorkspace({ routeLayer, location, field, fieldSpec,
                               alerts, onCorridor }: Props) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const particlesRef = useRef<ParticleLayer | null>(null);
  const basemapRef = useRef(0);
  const failuresRef = useRef(0);
  const [styleLoaded, setStyleLoaded] = useState(false);

  /* -------------------------------------------------------------- init */
  useEffect(() => {
    if (!mapContainer.current || mapRef.current) return;

    // The initial style contains NO remote source: MapLibre holds a style
    // unloaded until its sources resolve, so a blocked basemap would stall
    // style.load and nothing else would ever initialise (F-51).
    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: {
        version: 8, sources: {},
        layers: [{ id: 'bg', type: 'background',
                   paint: { 'background-color': '#050b14' } }]
      },
      center: [76.26, 9.93], zoom: 6.4, attributionControl: { compact: true }
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }),
                   'bottom-right');
    mapRef.current = map;

    // 'load' waits for TILES, not for the style, so poll for real readiness
    // and give up loudly rather than half-initialising (F-52).
    let cancelled = false;
    let ready = false;
    const deadline = Date.now() + 15000;
    const attempt = () => {
      if (cancelled || ready || !mapRef.current) return;
      if (!map.isStyleLoaded()) {
        if (Date.now() > deadline) {
          console.warn('map style never became ready; running without layers');
          return;
        }
        setTimeout(attempt, 250);
        return;
      }
      // `style.load` and the poll can both fire. Without this guard the second
      // one built a SECOND ParticleLayer over the first, leaking its listeners.
      ready = true;
      addBasemap(map, 0);
      loadBoundaries(map);
      if (canvasRef.current) {
        particlesRef.current = new ParticleLayer(canvasRef.current, map);
      }
      setStyleLoaded(true);
    };
    map.once('style.load', attempt);
    attempt();

    map.on('error', (e: maplibregl.ErrorEvent & { sourceId?: string }) => {
      if (e?.error) console.warn('map:', e.error.message || e.error);
      const src = e?.sourceId;
      if (src === 'base' && ++failuresRef.current === 4) {
        addBasemap(map, basemapRef.current + 1);
      }
    });

    return () => {
      cancelled = true;
      particlesRef.current?.destroy();
      particlesRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  function addBasemap(map: maplibregl.Map, i: number) {
    basemapRef.current = i;
    failuresRef.current = 0;
    if (i >= BASEMAPS.length) {
      console.warn('no basemap loaded; the sea is drawn without one');
      return;
    }
    const b = BASEMAPS[i];
    try {
      drop(map, ['base'], ['base']);
      map.addSource('base', {
        type: 'raster', tiles: [b.url], tileSize: 256, attribution: b.attribution
      });
      map.addLayer({
        id: 'base', type: 'raster', source: 'base',
        paint: { 'raster-opacity': b.opacity, 'raster-saturation': b.saturation }
      }, map.getLayer('eez-fill') ? 'eez-fill' : undefined);
    } catch (e) {
      console.warn(`basemap ${b.id} failed:`, e);
      addBasemap(map, i + 1);
    }
  }

  function loadBoundaries(map: maplibregl.Map) {
    fetchBoundaries().then((gj) => {
      if (!mapRef.current || map.getSource('eez')) return;
      map.addSource('eez', { type: 'geojson', data: gj as FeatureCollection });
      map.addLayer({ id: 'eez-fill', type: 'fill', source: 'eez',
        paint: { 'fill-color': '#4fd1c5', 'fill-opacity': 0.045 } });
      map.addLayer({ id: 'eez-line', type: 'line', source: 'eez',
        paint: { 'line-color': '#4fd1c5', 'line-width': 1.1,
                 'line-opacity': 0.5, 'line-dasharray': [3, 2] } });
    }).catch(() => { /* boundaries are chrome; the verdict does not need them */ });
  }

  /* ------------------------------------------------------------- fields */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleLoaded) return;

    const clear = () => drop(map, ['f-scalar', 'chl-median', 'chl-median-glow'],
                            ['f-scalar', 'chl-median']);

    if (!field || !fieldSpec) {
      particlesRef.current?.stop();
      clear();
      return;
    }

    if (fieldSpec.vector) {
      clear();
      particlesRef.current?.set(field);
      return;
    }

    particlesRef.current?.stop();
    const raster = rasteriseScalar(fieldSpec, field);
    clear();
    if (raster) {
      map.addSource('f-scalar', {
        type: 'image', url: raster.url,
        coordinates: raster.coordinates as [[number, number], [number, number],
                                           [number, number], [number, number]]
      });
      map.addLayer({
        id: 'f-scalar', type: 'raster', source: 'f-scalar',
        paint: { 'raster-opacity': 0.72, 'raster-fade-duration': 350 }
      }, map.getLayer('eez-fill') ? 'eez-fill' : undefined);
    }

    /* The local-median ring (Tier 1 #3).
     *
     * FISHING_SUITABILITY does not judge chlorophyll in absolute terms; it
     * judges the RATIO to the local median, so "1.03x the local median" is the
     * actual finding. Drawn as a contour at the median itself, the reasoning
     * becomes visible: inside the ring is richer than the neighbourhood,
     * outside is poorer, and the position sits on one side of it. Without the
     * ring the reader sees a colour ramp and has to take the comparison on
     * trust. */
    if (fieldSpec.name === 'chlorophyll') {
      const median = fieldMedian(field);
      const iso = median !== null ? isoline(field, median) : null;
      if (iso) {
        map.addSource('chl-median', { type: 'geojson', data: iso });
        map.addLayer({ id: 'chl-median-glow', type: 'line', source: 'chl-median',
          paint: { 'line-color': '#f8fafc', 'line-width': 5,
                   'line-opacity': 0.13, 'line-blur': 4 } });
        map.addLayer({ id: 'chl-median', type: 'line', source: 'chl-median',
          paint: { 'line-color': '#f8fafc', 'line-width': 1.3,
                   'line-opacity': 0.75, 'line-dasharray': [2, 2] } });
      }
    }
  }, [field, fieldSpec, styleLoaded]);

  /* -------------------------------------------------------------- route */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleLoaded) return;
    let cancelled = false;

    drop(map, ['route-glow', 'route-casing', 'route-corridor', 'route'], ['route']);
    if (!routeLayer) {
      onCorridor?.(null);
      return;
    }

    const coords = (routeLayer.data.geometry?.coordinates ?? []) as [number, number][];
    if (!coords.length) return;

    map.addSource('route', { type: 'geojson', data: tintRoute(coords, null) });

    // A wide dark casing under a bright line: without it the route disappears
    // over a pale basemap, which is how a drawn route can still be invisible.
    map.addLayer({ id: 'route-glow', type: 'line', source: 'route',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#010409', 'line-width': 11,
               'line-opacity': 0.5, 'line-blur': 3 } });
    map.addLayer({ id: 'route-casing', type: 'line', source: 'route',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#0b1220', 'line-width': 7, 'line-opacity': 0.9 } });

    /* The corridor (Tier 2 #8): the route tinted by the wave height along it.
     * A route is a recommendation about a PATH, and a single verdict for the
     * whole path hides that its risk is not uniform. Segments over a masked
     * cell keep the neutral colour and are declared in the legend rather than
     * being given a neighbour's value. */
    map.addLayer({ id: 'route-corridor', type: 'line', source: 'route',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-width': 4.5,
        'line-opacity': 0.95,
        'line-color': [
          'case', ['!', ['get', 'known']], '#94a3b8',
          ['interpolate', ['linear'], ['get', 'v'],
            ...WAVE_STOPS.flatMap(([v, c]) => [v, c])]
        ]
      } });
    map.addLayer({ id: 'route', type: 'line', source: 'route',
      layout: { 'line-cap': 'butt', 'line-join': 'round' },
      paint: { 'line-color': '#f8fafc', 'line-width': 1.6, 'line-opacity': 0.85,
               'line-dasharray': [0, 3, 3, 3] } });

    const seq: [number, number, number, number][] = [
      [0, 3, 3, 3], [0.5, 3, 2.5, 3], [1, 3, 2, 3], [1.5, 3, 1.5, 3],
      [2, 3, 1, 3], [2.5, 3, 0.5, 3], [3, 3, 0, 3]
    ];
    let step = 0;
    const timer = window.setInterval(() => {
      if (!mapRef.current || !map.getLayer('route')) return;
      map.setPaintProperty('route', 'line-dasharray', seq[step++ % seq.length]);
    }, 85);

    const bounds = new maplibregl.LngLatBounds();
    coords.forEach((c) => bounds.extend(c));
    const wide = window.innerWidth > 1120;
    map.fitBounds(bounds, {
      padding: wide ? { top: 90, bottom: 120, left: 460, right: 400 }
                    : { top: 130, bottom: 160, left: 30, right: 30 },
      duration: 900
    });

    // Wave data for the corridor is fetched AFTER the route is drawn and never
    // gates it: the route is the answer, the tint is an enhancement.
    const c = bounds.getCenter();
    const nw = bounds.getNorthWest();
    const radius = Math.min(800, Math.max(120, nw.distanceTo(c) / 1000 * 1.15));
    fetchField('waves', c.lat, c.lng, Math.round(radius))
      .then((waves) => {
        if (cancelled || !mapRef.current) return;
        const src = map.getSource('route') as maplibregl.GeoJSONSource | undefined;
        if (!src) return;
        const tinted = tintRoute(coords, waves);
        src.setData(tinted);
        const known = tinted.features
          .map((f) => (f.properties?.v ?? null) as number | null)
          .filter((v: number | null): v is number => v !== null);
        onCorridor?.(known.length
          ? { unit: waves.unit, min: Math.min(...known), max: Math.max(...known) }
          : null);
      })
      .catch(() => { if (!cancelled) onCorridor?.(null); });

    return () => { cancelled = true; window.clearInterval(timer); };
  }, [routeLayer, styleLoaded, onCorridor]);

  /* ------------------------------------------------------ geofence rings */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleLoaded) return;
    drop(map, ['ring-fill', 'ring-line', 'ring-label'], ['rings']);

    const withDistance = (alerts ?? []).filter((a) => a.distance_km != null);
    if (!withDistance.length || !location?.lat) return;

    /* An alert that says "0.8 km from the EEZ" is a number the reader has to
     * hold in their head while looking at a map that shows no such distance.
     * Drawing it as a ring around the position puts the two in the same frame:
     * the boundary line either does or does not cross the ring. */
    const rings = withDistance.map((a) => {
      const f = ringPolygon(location.lat, location.lon, a.distance_km as number);
      f.properties = {
        ...f.properties,
        label: `${a.boundary_type} · ${a.distance_km} km`,
        severity: a.severity,
        inside: a.inside
      };
      return f;
    });
    map.addSource('rings', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: rings } as FeatureCollection
    });
    map.addLayer({ id: 'ring-fill', type: 'fill', source: 'rings',
      paint: {
        'fill-color': ['match', ['get', 'severity'],
          'warning', '#f43f5e', 'caution', '#fbbf24', '#4fd1c5'],
        'fill-opacity': 0.045
      } },
      map.getLayer('route-glow') ? 'route-glow' : undefined);
    map.addLayer({ id: 'ring-line', type: 'line', source: 'rings',
      paint: {
        'line-color': ['match', ['get', 'severity'],
          'warning', '#f43f5e', 'caution', '#fbbf24', '#4fd1c5'],
        'line-width': 1.1, 'line-opacity': 0.6, 'line-dasharray': [1, 2]
      } },
      map.getLayer('route-glow') ? 'route-glow' : undefined);
  }, [alerts, location, styleLoaded]);

  /* ------------------------------------------------------------ markers */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleLoaded || !location?.lat) return;

    const features: Feature[] = [{
      type: 'Feature', properties: { k: 'origin' },
      geometry: { type: 'Point', coordinates: [location.lon, location.lat] }
    }];
    if (location.dest_lat != null && location.dest_lon != null) {
      features.push({
        type: 'Feature', properties: { k: 'dest' },
        geometry: { type: 'Point', coordinates: [location.dest_lon, location.dest_lat] }
      });
    }
    const data = { type: 'FeatureCollection', features } as FeatureCollection;

    // Re-add the LAYERS, not just the data: another effect may have dropped
    // them, and a source without its layers renders nothing at all.
    if (map.getSource('here')) {
      (map.getSource('here') as maplibregl.GeoJSONSource).setData(data);
    } else {
      map.addSource('here', { type: 'geojson', data });
    }
    if (!map.getLayer('here-origin')) {
      map.addLayer({ id: 'here-origin', type: 'circle', source: 'here',
        filter: ['==', ['get', 'k'], 'origin'],
        paint: { 'circle-radius': 6, 'circle-color': '#4fd1c5',
                 'circle-stroke-width': 2,
                 'circle-stroke-color': 'rgba(255,255,255,.85)' } });
      map.addLayer({ id: 'here-dest', type: 'circle', source: 'here',
        filter: ['==', ['get', 'k'], 'dest'],
        paint: { 'circle-radius': 6, 'circle-color': '#fbbf24',
                 'circle-stroke-width': 2,
                 'circle-stroke-color': 'rgba(255,255,255,.85)' } });
    }
    if (!routeLayer) map.easeTo({ center: [location.lon, location.lat], zoom: 7.2 });
  }, [location, routeLayer, styleLoaded]);

  return (
    <>
      <div ref={mapContainer} className="map-pane" />
      <canvas ref={canvasRef} className="particle-canvas" />
    </>
  );
}
