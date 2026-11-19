import type { CSSProperties } from 'react';
import { type as ty, surface } from './tokens';
/** Shared by connection management and agent-requested setup. */
export const connectionStyle = {
 card: { padding: '20px 0', border: 'none', borderBottom: '1px solid var(--line)', borderRadius: 0, background: 'transparent', minWidth: 0 } as CSSProperties,
 input: { ...ty.body, width: '100%', boxSizing: 'border-box', padding: '9px 11px', border: '1px solid var(--line2)', borderRadius: 7, background: surface.rail, color: 'var(--t1)', marginBottom: 12 } as CSSProperties,
 button: { ...ty.control, border: '1px solid var(--line2)', borderRadius: 7, padding: '8px 12px', background: surface.raised, color: 'var(--t1)', cursor: 'pointer' } as CSSProperties,
 primary: { background: 'var(--accent)', color: 'var(--on-accent)', borderColor: 'transparent' } as CSSProperties,
};
