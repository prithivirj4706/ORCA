export interface ORCAField {
  field: string;
  label: string;
  kind: "scalar" | "vector";
  unit: string | null;
  lats: number[];
  lons: number[];
  values?: (number | null)[][];
  u?: (number | null)[][];
  v?: (number | null)[][];
  speed?: (number | null)[][];
  range: { min: number; max: number };
  cells: { total: number; valid: number; coverage: number };
  valid_time: string;
  source: string;
  source_id: string;
  dataset: string;
  advisory_only: boolean;
}

/** `[low, high]`, either end null for an open band. */
export type BandEdges = [number | null, number | null];

export interface ORCADriver {
  factor: string;
  value: number | boolean | null;
  unit: string | null;
  band: string | null;
  contribution: 'limiting' | 'supporting' | 'context';
  threshold_id?: string | null;
  evidence_id?: string | null;
  // The edges this factor was actually judged against. Present for numeric
  // factors; absent for booleans, which have no axis. Without them a gauge
  // can only place the pin inside its band, never at a true position.
  bands?: Record<string, BandEdges> | null;
  higher_is_worse?: boolean | null;
}

export interface ORCAAssessment {
  domain: string;
  verdict: string;
  confidence: string;
  rationale: string;
  // A driver's value may be a number, or a BOOLEAN for containment
  // (regulatory) and presence (warnings, advisories).
  drivers: ORCADriver[];
  not_evaluated: { factor: string; reason: string; detail: string | null }[];
  missing_required: string[];
  verdict_capped_by: string[];
  limiting_factor: string | null;
}

/** One retrieved value's validity, against the analysis window. */
export interface ORCATemporalEntry {
  provenance_id: string;
  tool: string | null;
  parameter: string | null;
  value_kind: string | null;
  source: string | null;
  source_id: string | null;
  dataset: string | null;
  valid_time: string | null;
  valid_from: string | null;
  valid_to: string | null;
  reference_time: string | null;
  lead_time_h: number | null;
  representativeness: string | null;
  retrieved_at: string | null;
  age_s: number | null;
  /** Retrieved AND used. A false here is the interesting row. */
  used: boolean;
  derived_via: string | null;
  evidence_id: string | null;
  domain: string | null;
  excluded_reason: string | null;
  excluded_detail: string | null;
}

export interface ORCATemporalAlignment {
  window: { start_time: string | null; end_time: string | null };
  generated_at: string;
  entries: ORCATemporalEntry[];
}

export interface ORCAEvidence {
  evidence_id: string;
  domain: string;
  statement: string;
  parameter: string;
  value: any;
  unit: string | null;
  value_kind: string;
  provenance_id: string;
  weight: string;
}

export interface ORCANotEvaluated {
  factor: string;
  reason: string;
  detail: string | null;
  tool?: string | null;
}

export interface ORCAPlan {
  domains: string[];
  required_evidence: string[];
  steps: { step_id: string; tool: string; necessity: string }[];
  /** Capabilities ORCA planned for and could not fill. First-class content. */
  unavailable: { evidence?: string; tool?: string; reason?: string }[];
  reasoning_summary: string;
}

/** What the router was actually steered by — see `analysis.geo_reason`. */
export interface ORCARouteProps {
  waypoints?: number;
  length_km?: number;
  navigability?: string;
  advisory_only?: boolean;
  /** Parameters that penalised the search. EMPTY means distance-only. */
  steered_by?: string[];
  fields_unavailable?: { parameter: string; reason: string; detail: string }[];
  objective?: string;
  note?: string;
}

export interface ORCAMapLayer {
  id: string;
  type: string;
  name: string;
  data: any; // GeoJSON Feature
}

export interface ORCAResponse {
  thread_id?: string;
  language?: string;
  intent?: string;
  resolved_location?: { lat: number; lon: number; label: string; dest_lat?: number; dest_lon?: number };
  resolved_time_window?: { start_time: string; end_time: string };
  // The API returns WHICH detail is missing ('location', 'time_window',
  // 'destination', 'intent') or null -- not a boolean.
  clarification_needed?: string | null;
  plan?: ORCAPlan | null;
  assessments?: ORCAAssessment[];
  evidence?: ORCAEvidence[];
  alerts?: ORCAAlert[];
  map_layers?: ORCAMapLayer[];
  claims?: any[];
  not_evaluated?: ORCANotEvaluated[];
  temporal_alignment?: ORCATemporalAlignment;
  resolution_notes?: string[];
  disposition?: string;
  recommendation?: {
    category: string; headline: string; is_official_advisory: boolean;
    // The composed answer, and the only field written in the user's
    // language. `headline` is always English.
    narrative?: string;
  };
  trace?: ORCATraceEvent[];
}

export interface ORCAAlert {
  kind: 'approaching' | 'leaving' | 'inside';
  boundary_type: string;
  severity: 'info' | 'caution' | 'warning';
  distance_km: number | null;
  inside: boolean;
  name: string | null;
  dataset_version: string | null;
  advisory_only: boolean;
}

export interface ORCATraceEvent {
  node: string;
  status: string;
  duration_ms?: number;
  summary?: string;
  tool?: string;
  source?: string;
  codes?: string[];
  fallback_used?: boolean;
}

export interface ORCASource {
  tool: string;
  description: string;
  yields: string[];
  domains: string[];
  available: boolean;
  unavailable_reason: string | null;
}

export interface ORCASourceHealth { sources: ORCASource[] }

export interface ORCABoundaries {
  type: 'FeatureCollection';
  features: any[];
  dataset_version?: string;
  snapshot_version?: string;
}

export interface ORCAProvenance {
  provenance_id: string;
  parameter: string;
  unit: string | null;
  value_kind: string;
  source: string;
  source_id: string;
  organisation?: string | null;
  dataset?: string | null;
  access_method?: string | null;
  retrieved_at?: string | null;
  licence_reference?: string | null;
  derivation?: {
    method: string; method_version: string;
    inputs: string[]; params: Record<string, unknown>;
  } | null;
}
