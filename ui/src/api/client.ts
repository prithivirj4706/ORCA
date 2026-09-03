/* API client.
 *
 * Paths are RELATIVE. The bundle is served from FastAPI's own static mount in
 * production, and Vite proxies /v1 to the backend in development (see
 * vite.config.ts), so no origin is ever hard-coded. An absolute
 * http://localhost:8000 broke the moment the app was served from anywhere else.
 */
import type {
  ORCABoundaries, ORCAField, ORCAResponse, ORCASourceHealth, ORCAProvenance
} from '../types/api';

export const API_BASE = '/v1';

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error((await res.text()) || `${res.status} on ${path}`);
  return res.json() as Promise<T>;
}

export async function askOrca(query: string, thread_id?: string): Promise<ORCAResponse> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(thread_id ? { query, thread_id } : { query })
  });
  if (!res.ok) throw new Error('Chat request failed');
  return res.json();
}

export const fetchField = (name: string, lat: number, lon: number, radiusKm: number) =>
  get<ORCAField>(`/field/${name}?lat=${lat}&lon=${lon}&radius_km=${radiusKm}`);

export const fetchBoundaries = () =>
  get<ORCABoundaries>('/boundaries?min_lat=2&min_lon=64&max_lat=26&max_lon=92');

export const fetchSourceHealth = () => get<ORCASourceHealth>('/health/sources');

export const fetchProvenance = (thread: string, provenanceId: string) =>
  get<{ provenance: ORCAProvenance[] }>(
    `/runs/${thread}/provenance?provenance_id=${encodeURIComponent(provenanceId)}`);
