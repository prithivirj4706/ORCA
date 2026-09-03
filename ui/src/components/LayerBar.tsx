import type { FieldSpec } from '../lib/fields';

interface Props {
  specs: FieldSpec[];
  active: string | null;
  loading: string | null;
  onToggle: (spec: FieldSpec) => void;
}

/* One field at a time. Two particle systems, or a raster under particles, is
 * noise rather than information. */
export function LayerBar({ specs, active, loading, onToggle }: Props) {
  return (
    <div className="layer-bar">
      {specs.map((s) => (
        <button
          key={s.name}
          className={`lbtn${active === s.name ? ' on' : ''}${loading === s.name ? ' loading' : ''}`}
          onClick={() => onToggle(s)}
          title={s.vector ? 'Animated vector field' : 'Gridded field'}
        >
          <span className="ld" />
          {s.label}
        </button>
      ))}
    </div>
  );
}
