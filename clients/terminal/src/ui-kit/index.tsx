/** term-ui-kit — shared primitives + icons painted from the prototype's dark tokens (globals.css). */
import type { CSSProperties } from "react";

const PATHS: Record<string, string> = {
  radio: "M16.2 7.8a6 6 0 0 1 0 8.4M19.1 4.9a10 10 0 0 1 0 14.2M7.8 16.2a6 6 0 0 1 0-8.4M4.9 19.1a10 10 0 0 1 0-14.2M12 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4",
  msg: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
  panel: "M3 3h18v18H3zM9 3v18",
  mail: "M2 4h20v16H2zM22 7l-10 5L2 7",
  cal: "M3 4h18v18H3zM16 2v4M8 2v4M3 10h18",
  tasks: "M11 3 8 6 6.5 4.5M11 9 8 12l-1.5-1.5M11 15l-3 3-1.5-1.5M14 5h7M14 11h7M14 17h7",
  zap: "M13 2 3 14h9l-1 8 10-12h-9z",
  send: "M22 2 11 13M22 2l-7 20-4-9-9-4z",
  paperclip: "M21.4 11.6 12 21a6 6 0 0 1-8.5-8.5l10-10a4 4 0 0 1 5.7 5.7l-10 10a2 2 0 1 1-2.8-2.8l9.3-9.3",
  plus: "M5 12h14M12 5v14",
  x: "M18 6 6 18M6 6l12 12",
  file: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6",
  search: "M21 21l-4.3-4.3M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z",
  edit: "M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7M18.5 2.5a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4z",
  git: "M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6M18 9a9 9 0 0 1-9 9",
  web: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20M2 12h20M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20z",
  check: "M20 6 9 17l-5-5",
  user: "M20 21a8 8 0 1 0-16 0M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8",
  building: "M3 21h18M6 21V4a1 1 0 0 1 1-1h7a1 1 0 0 1 1 1v17M19 21V9l-4-2M9 7h2M9 11h2M9 15h2",
  tag: "M3 3h7l11 11-7 7L3 10zM7 7h.01",
  chevR: "M9 6l6 6-6 6",
  spark: "M12 3l1.6 5.4L19 10l-5.4 1.6L12 17l-1.6-5.4L5 10l5.4-1.6z",
  openIn: "M7 17 17 7M9 7h8v8",
  link: "M9 12a3 3 0 0 1 3-3h4a3 3 0 1 1 0 6h-2M15 12a3 3 0 0 1-3 3H8a3 3 0 1 1 0-6h2",
  arrowR: "M5 12h14M13 6l6 6-6 6",
  folder: "M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6",
  eyeOff: "M2 12s3.5-7 10-7a11 11 0 0 1 4 .7M22 12s-3.5 7-10 7a11 11 0 0 1-4-.7M3 3l18 18",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
};

export function Icon({ name, size = 18, style }: { name: string; size?: number; style?: CSSProperties }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
      strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" style={{ flex: "none", ...style }}
      aria-hidden="true">
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}
