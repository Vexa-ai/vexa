"use client";
/** Button and IconButton (guidelines §4.1–4.2). Variants and sizes are data attributes read by
 *  `controls.css`; every state — hover, focus-visible, pressed, disabled, loading — lives there.
 *  `primary` is the view's ONE "do this" action; `danger` is only the confirm of a destructive
 *  dialog. IconButton REQUIRES an accessible name (`label`, enforced by the type) and shows it as a
 *  tooltip; `pressed` makes it a toggle (`aria-pressed`). Loading keeps the width (a spinner
 *  replaces the icon) and sets `aria-busy`. */
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "danger-ghost";

type Base = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "style" | "className"> & { variant?: ButtonVariant; loading?: boolean };

export const Button = forwardRef<HTMLButtonElement, Base & { size?: "sm" | "md"; icon?: ReactNode; children: ReactNode }>(
  function Button({ variant = "secondary", size = "sm", icon, loading, disabled, children, type = "button", ...rest }, ref) {
    return (
      <button ref={ref} type={type} className="vx-btn" data-variant={variant} data-size={size}
        disabled={disabled} aria-busy={loading || undefined} {...rest}>
        {loading ? <Spinner size={14} /> : icon ? <span className="vx-btn-icon" aria-hidden>{icon}</span> : null}
        <span className="vx-btn-label">{children}</span>
      </button>
    );
  });

export const IconButton = forwardRef<HTMLButtonElement, Base & { label: string; size?: "xs" | "sm" | "md"; pressed?: boolean; children: ReactNode }>(
  function IconButton({ label, variant = "ghost", size = "sm", pressed, loading, disabled, children, type = "button", ...rest }, ref) {
    return (
      <button ref={ref} type={type} className="vx-btn vx-iconbtn" data-variant={variant} data-size={size}
        aria-label={label} title={label} aria-pressed={pressed} disabled={disabled} aria-busy={loading || undefined} {...rest}>
        {loading ? <Spinner size={14} /> : <span aria-hidden className="vx-btn-icon">{children}</span>}
      </button>
    );
  });
