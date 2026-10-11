"use client";
/** Input and Textarea (guidelines §4.3): a label ABOVE the field (never a placeholder alone), a
 *  32px control on surface-2 with the control border, the focus ring on :focus-visible, and an
 *  error line linked through `aria-describedby` with `aria-invalid`. */
import { useId, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { AlertCircle } from "lucide-react";

type Common = { label: string; hideLabel?: boolean; error?: string; hint?: string };

function Wrap({ id, label, hideLabel, error, hint, children }: Common & { id: string; children: React.ReactNode }) {
  return (
    <div className="vx-field" data-invalid={error ? "" : undefined}>
      <label htmlFor={id} className={hideLabel ? "vx-sr" : "vx-field-label"}>{label}</label>
      {children}
      {hint && !error && <div id={`${id}-hint`} className="vx-field-hint">{hint}</div>}
      {error && <div id={`${id}-err`} className="vx-field-error" role="alert"><AlertCircle size={14} strokeWidth={1.75} aria-hidden />{error}</div>}
    </div>
  );
}
const describedBy = (id: string, error?: string, hint?: string) => (error ? `${id}-err` : hint ? `${id}-hint` : undefined);

export function Input({ label, hideLabel, error, hint, ...rest }: Common & Omit<InputHTMLAttributes<HTMLInputElement>, "style" | "className">) {
  const id = useId();
  return <Wrap id={id} label={label} hideLabel={hideLabel} error={error} hint={hint}>
    <input id={id} className="vx-input" aria-invalid={error ? true : undefined} aria-describedby={describedBy(id, error, hint)} {...rest} />
  </Wrap>;
}
export function Textarea({ label, hideLabel, error, hint, ...rest }: Common & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "style" | "className">) {
  const id = useId();
  return <Wrap id={id} label={label} hideLabel={hideLabel} error={error} hint={hint}>
    <textarea id={id} className="vx-input vx-textarea" aria-invalid={error ? true : undefined} aria-describedby={describedBy(id, error, hint)} {...rest} />
  </Wrap>;
}
