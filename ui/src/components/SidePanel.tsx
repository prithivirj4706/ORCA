import type { ReactNode } from 'react';

interface Props {
  title: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}

export function SidePanel({ title, open, onClose, children }: Props) {
  return (
    <aside className={`side-panel${open ? ' open' : ''}`}>
      <div className="phead">
        <span>{title}</span>
        <span className="x" onClick={onClose} role="button" aria-label="Close">✕</span>
      </div>
      <div className="pbody">{children}</div>
    </aside>
  );
}
