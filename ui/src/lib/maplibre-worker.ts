/* MapLibre's worker URL, resolved by the bundler instead of at runtime.
 *
 * maplibre-gl v6 ships its worker as a SEPARATE file and, when no explicit URL
 * is configured, derives one from `import.meta.url`:
 *
 *     new URL('./maplibre-gl-worker.mjs', import.meta.url)
 *
 * That assumes the library is served as loose ESM next to its own siblings.
 * Once Vite bundles it, `import.meta.url` is the APPLICATION bundle's URL, so
 * the worker resolves to /ui/assets/maplibre-gl-worker.mjs -- a file no build
 * ever emits, because the specifier is computed and therefore invisible to the
 * bundler.
 *
 * The failure is silent and total: the request 404s, no error reaches the
 * console, and every source that is parsed IN THE WORKER -- which is every
 * GeoJSON and vector source -- stays `loaded: false` forever. Raster tiles keep
 * working, so the basemap paints and only the data disappears: no route, no
 * EEZ boundaries, no markers, on a map that otherwise looks healthy.
 *
 * `?worker&url` makes Vite bundle the worker (with its ~500 KB
 * maplibre-gl-shared.mjs dependency, which a worker cannot share with the main
 * thread) and hand back a real emitted URL. `config.WORKER_URL` takes
 * precedence over the derived path, so setting it before the first Map is
 * constructed is all that is required.
 */
import { setWorkerUrl } from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';

setWorkerUrl(workerUrl);
