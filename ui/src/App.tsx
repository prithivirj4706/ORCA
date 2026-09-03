import { useCallback, useEffect, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MapWorkspace } from './components/MapWorkspace';
import { Conversation } from './components/Conversation';
import { LayerBar } from './components/LayerBar';
import { Legend } from './components/Legend';
import { AgentTrace } from './components/AgentTrace';
import { SidePanel } from './components/SidePanel';
import { SourceHealth } from './components/SourceHealth';
import { ProvenancePanel } from './components/ProvenancePanel';
import { fetchField, fetchSourceHealth } from './api/client';
import { FIELD_SPECS, type FieldSpec } from './lib/fields';
import type {
  ORCAField, ORCAMapLayer, ORCAResponse, ORCASource, ORCATraceEvent
} from './types/api';
import type { CorridorInfo } from './components/Legend';
import type { ORCARouteProps } from './types/api';
// Imported last: maplibre-gl.css ships `.maplibregl-map{position:relative}` and
// would otherwise win the cascade over our own single-class rules.
import './App.css';

const queryClient = new QueryClient();

type PanelKind = 'trace' | 'sources' | 'provenance';

function App() {
  const [result, setResult] = useState<ORCAResponse | null>(null);
  const [trace, setTrace] = useState<ORCATraceEvent[]>([]);
  const [traceLive, setTraceLive] = useState(false);
  const [panel, setPanel] = useState<PanelKind | null>(null);
  const [provenanceId, setProvenanceId] = useState<string | null>(null);
  const [sources, setSources] = useState<ORCASource[]>([]);

  const [fieldSpec, setFieldSpec] = useState<FieldSpec | null>(null);
  const [field, setField] = useState<ORCAField | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [fieldLoading, setFieldLoading] = useState<string | null>(null);
  const [corridor, setCorridor] = useState<CorridorInfo | null>(null);

  const routeLayer: ORCAMapLayer | null =
    result?.map_layers?.find((l) => l.id === 'optimized_route') ?? null;
  // What the router was steered by travels with the layer. The corridor tint
  // must not imply a steering that did not happen.
  const routeProps: ORCARouteProps | null =
    (routeLayer?.data?.properties as ORCARouteProps) ?? null;
  const location = result?.resolved_location ?? null;
  const centre = { lat: location?.lat ?? 9.93, lon: location?.lon ?? 76.26 };

  useEffect(() => {
    fetchSourceHealth().then((d) => setSources(d.sources)).catch(() => setSources([]));
  }, []);

  const handleTrace = useCallback((t: ORCATraceEvent[], live: boolean) => {
    setTrace(t);
    setTraceLive(live);
    // Open the trace while a run is in flight -- watching the plan form is the
    // point -- but never over a clarifying question (F-57); Conversation
    // surfaces that, and the effect below closes the panel for it.
    if (live && t.length) setPanel((p) => (p === 'trace' || p === null ? 'trace' : p));
  }, []);

  const handleResult = useCallback((r: ORCAResponse) => {
    setResult(r);
    if (r.clarification_needed) setPanel(null);
  }, []);

  const handleEvidenceClick = useCallback((pid: string) => {
    setProvenanceId(pid);
    setPanel('provenance');
  }, []);

  const toggleField = useCallback((spec: FieldSpec) => {
    if (fieldSpec?.name === spec.name) {
      setFieldSpec(null);
      setField(null);
      setFieldError(null);
      return;
    }
    setFieldLoading(spec.name);
    setFieldError(null);
    fetchField(spec.name, centre.lat, centre.lon, spec.radiusKm)
      .then((d) => {
        setFieldSpec(spec);
        setField(d);
      })
      .catch((e: Error) => {
        // A layer that fails is ABSENT with a reason, never a blank map.
        setFieldSpec(spec);
        setField(null);
        setFieldError(e.message);
      })
      .finally(() => setFieldLoading(null));
  }, [fieldSpec, centre.lat, centre.lon]);

  const available = sources.filter((s) => s.available).length;
  const panelTitle =
    panel === 'sources' ? 'Source health'
      : panel === 'provenance' ? 'Provenance'
        : 'Agent trace';

  return (
    <QueryClientProvider client={queryClient}>
      <MapWorkspace
        routeLayer={routeLayer}
        location={location}
        field={field}
        fieldSpec={fieldSpec}
        alerts={result?.alerts}
        onCorridor={setCorridor}
      />
      <div className="caustics" />
      <div className="vignette" />

      <header className="top-bar">
        <div className="brand">
          <b>ORCA</b>
          <span>Ocean Reasoning &amp; Collaborative Agents</span>
        </div>
        <div className="spacer" />
        {result?.language && (
          <span className="lang-badge">{result.language.toUpperCase()}</span>
        )}
        <button className="health" onClick={() => setPanel('sources')}>
          <span className={`dot${available < sources.length ? ' warn' : ''}`} />
          {sources.length ? `${available}/${sources.length} sources` : '…'}
        </button>
        {trace.length > 0 && panel !== 'trace' && (
          <button className="health" onClick={() => setPanel('trace')}>
            trace
          </button>
        )}
      </header>

      <main className="panel left-panel">
        <div className="phead">
          <span>Conversation</span>
        </div>
        <Conversation
          onResult={handleResult}
          onTrace={handleTrace}
          onEvidenceClick={handleEvidenceClick}
        />
      </main>

      <SidePanel title={panelTitle} open={panel !== null} onClose={() => setPanel(null)}>
        {panel === 'trace' && <AgentTrace trace={trace} live={traceLive} />}
        {panel === 'sources' && <SourceHealth sources={sources} />}
        {panel === 'provenance' && (
          <ProvenancePanel thread={result?.thread_id} provenanceId={provenanceId} />
        )}
      </SidePanel>

      <LayerBar
        specs={FIELD_SPECS}
        active={field || fieldError ? fieldSpec?.name ?? null : null}
        loading={fieldLoading}
        onToggle={toggleField}
      />
      <Legend spec={fieldSpec} field={field} error={fieldError}
              corridor={corridor} route={routeProps} />
    </QueryClientProvider>
  );
}

export default App;
