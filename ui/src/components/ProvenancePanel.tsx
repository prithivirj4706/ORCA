/* The provenance chain, as a chain.
 *
 * A flat table of fields answers "what is recorded" but not "where did this
 * number come from", which is the question a reader actually has. The record
 * already describes three levels -- the value, the derivation that produced it,
 * and the source it was retrieved from -- so they are drawn as levels, in the
 * direction the data travelled: source first, then method, then value.
 *
 * A value with no derivation has two levels, not a blank one. Drawing an empty
 * "derivation" step for an observation would imply a computation happened.
 */
import { useEffect, useState } from 'react';
import { fetchProvenance } from '../api/client';
import { FreshnessDot } from './Freshness';
import type { ORCAProvenance } from '../types/api';

const Row = ({ k, v }: { k: string; v: unknown }) =>
  v == null || v === '' ? null : (
    <div className="prov-row">
      <span className="prov-k">{k}</span>
      <span className="prov-v">{String(v)}</span>
    </div>
  );

function Level({ n, title, tag, children }:
  { n: number; title: string; tag: string; children: React.ReactNode }) {
  return (
    <div className="plevel">
      <div className="plevel-rail">
        <span className="plevel-n">L{n}</span>
      </div>
      <div className="plevel-body">
        <div className="plevel-head">
          {title} <span className="plevel-tag">{tag}</span>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ProvenancePanel({ thread, provenanceId }:
  { thread?: string; provenanceId: string | null }) {
  const [rec, setRec] = useState<ORCAProvenance | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Stamped when the record arrives, not read during render: a clock read
  // while rendering makes the output depend on WHEN React happened to re-run
  // the component.
  const [loadedAt, setLoadedAt] = useState<number | null>(null);

  useEffect(() => {
    if (!thread || !provenanceId) return;
    let live = true;
    setRec(null);
    setError(null);
    fetchProvenance(thread, provenanceId)
      .then((d) => {
        if (!live) return;
        setRec(d.provenance?.[0] ?? null);
        setLoadedAt(Date.now());
      })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [thread, provenanceId]);

  if (!provenanceId) return <div className="empty">Select an evidence id.</div>;
  if (error) return <div className="empty">Could not load provenance.</div>;
  if (!rec) return <div className="empty"><span className="spin" /></div>;

  const d = rec.derivation;
  const temporal = (rec as unknown as { temporal?: Record<string, unknown> }).temporal;
  const validTime = temporal?.valid_time as string | undefined;
  const age = validTime && loadedAt !== null
    ? (loadedAt - Date.parse(validTime)) / 1000
    : null;

  return (
    <div className="pchain">
      <div className="mono-sm pchain-id">{rec.provenance_id}</div>

      <Level n={1} title={rec.source ?? 'source'} tag="retrieved">
        <Row k="source id" v={rec.source_id} />
        <Row k="organisation" v={rec.organisation} />
        <Row k="dataset" v={rec.dataset} />
        <Row k="access" v={rec.access_method} />
        <Row k="retrieved" v={String(rec.retrieved_at ?? '').slice(0, 19).replace('T', ' ')} />
      </Level>

      {d ? (
        <Level n={2} title={d.method} tag={`v${d.method_version}`}>
          <Row k="inputs" v={(d.inputs || []).join(', ')} />
          <Row k="params" v={JSON.stringify(d.params || {})} />
          <div className="cov">
            Recorded so the value can be recomputed from its inputs, not merely
            attributed.
          </div>
        </Level>
      ) : (
        <Level n={2} title="no derivation" tag="as published">
          <div className="cov">
            Reported as retrieved. No ORCA computation stands between the source
            and this number.
          </div>
        </Level>
      )}

      <Level n={3} title={rec.parameter} tag={rec.value_kind}>
        <Row k="unit" v={rec.unit} />
        {validTime && (
          <div className="prov-row">
            <span className="prov-k">valid</span>
            <span className="prov-v">
              <FreshnessDot ageSeconds={age} title={rec.parameter} />
              {String(validTime).slice(0, 19).replace('T', ' ')}Z
            </span>
          </div>
        )}
        <Row k="representativeness" v={temporal?.representativeness as string} />
      </Level>

      {rec.licence_reference && (
        <div className="disclaimer">{rec.licence_reference}</div>
      )}
    </div>
  );
}
