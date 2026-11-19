"use client";
/** Switch (guidelines §4): an on/off setting that takes effect at once — role="switch", its visible
 *  label is its accessible name, Space and Enter toggle, a focus ring, a 24px target. On is the
 *  accent as a selected state (outline and tint, never a solid fill and never a badge). */
export function Switch({ checked, onChange, label, disabled, hideLabel }: {
  checked: boolean; onChange: (on: boolean) => void; label: string; disabled?: boolean;
  /** the label stays the accessible name but is not drawn (a column whose header names it) */
  hideLabel?: boolean;
}) {
  return (
    <button type="button" role="switch" aria-checked={checked} disabled={disabled} className="vx-switch" aria-label={hideLabel ? label : undefined}
      onClick={(e) => { e.stopPropagation(); onChange(!checked); }}>
      <span className="vx-switch-track" aria-hidden><span className="vx-switch-thumb" /></span>
      {!hideLabel && <span className="vx-switch-label">{label}</span>}
    </button>
  );
}
