/** THE SHELL'S LAYOUT, AS ONE PURE FUNCTION (terminal design guidelines §3.1).
 *
 *  The shell has three panes: the RAIL (chat list), the CONVERSATION, and the PAGES panel. The
 *  conversation is the primary task, so it is the one pane that is protected: as the window narrows,
 *  the side panes give way first, in a fixed order — the rail folds to a 48px strip (and opens as a
 *  drawer over the conversation), then the pages panel stops docking and becomes a sheet.
 *
 *  Before this function the shell was a grid of two fixed 240px side columns and a centre allowed to
 *  reach zero: at 640px the conversation was 160px wide while both side panes kept their full width.
 *  That rule was upside down, and this file is where it is turned the right way up.
 *
 *  WHAT THE READER ASKED FOR AND WHAT FITS ARE SEPARATE STATE. `prefs` records the reader's choice
 *  (rail open, pages open); the mode records what the viewport allows. Nothing in here writes a
 *  preference, so an auto-collapse can never overwrite one — widening the window restores the
 *  reader's layout. Widths are remembered per mode and CLAMPED on read, never discarded.
 *
 *  Pure: no DOM, no storage, no React. `useShellLayout` feeds it and the shell renders the result. */

export type ShellMode = "wide" | "desktop" | "compact" | "narrow" | "single";
export type RailKind = "docked" | "strip" | "drawer";
export type PagesKind = "docked" | "collapsed" | "sheet" | "fullscreen";
export type Pane = "rail" | "pages";

/** What the reader asked for. `true` = open; a fresh profile opens both. */
export type ShellPrefs = { railOpen: boolean; pagesOpen: boolean };
/** Widths the reader dragged to, per mode. Absent = the mode's default. */
export type StoredWidths = Partial<Record<ShellMode, Partial<Record<Pane, number>>>>;

export type Bounds = { min: number; max: number; def: number };

export type ShellLayout = {
  mode: ShellMode;
  railKind: RailKind;
  pagesKind: PagesKind;
  /** The grid's three columns, e.g. `240px minmax(0, 1fr) 480px`. */
  columns: string;
  /** Rendered widths. `rail` / `pages` are the GRID columns (0 when the pane is an overlay);
   *  `sheet` is the overlay width when the pages pane is a sheet; `drawer` the rail drawer's. */
  widths: { rail: number; pages: number; chat: number; sheet: number; drawer: number };
  /** The splitter ranges for a docked pane, already clamped to this viewport; absent when the pane
   *  is not docked (nothing to drag). */
  bounds: { rail?: Bounds; pages?: Bounds };
  /** The conversation's floor in this mode (0 = it takes the full width). */
  chatMin: number;
};

/** Breakpoints, viewport px (the lower bound of each mode). */
export const BREAKPOINTS = { wide: 1440, desktop: 1200, compact: 960, narrow: 720 } as const;
/** The collapsed rail: an icon strip. */
export const STRIP_W = 48;
/** A collapsed pages panel keeps one reopen control at the edge (≥ 24px: WCAG 2.5.8 target). */
export const EDGE_W = 24;
/** The rail drawer, when the rail is an overlay. */
export const DRAWER_W = 240;
/** The pages sheet, when the pages panel is an overlay in narrow mode. */
export const SHEET_MAX = 480;

type ModeSpec = {
  chatMin: number;
  rail?: Bounds;            // present = the rail may dock in this mode
  pages?: Bounds & { frac?: number };   // present = the pages panel may dock in this mode
};

/** The table in guidelines §3.1, as data. */
export const MODES: Record<ShellMode, ModeSpec> = {
  wide: { chatMin: 560, rail: { def: 240, min: 200, max: 320 }, pages: { def: 480, min: 320, max: Infinity, frac: 0.6 } },
  desktop: { chatMin: 480, rail: { def: 240, min: 200, max: 280 }, pages: { def: 400, min: 320, max: Infinity } },
  compact: { chatMin: 440, pages: { def: 360, min: 320, max: Infinity } },
  narrow: { chatMin: 0 },
  single: { chatMin: 0 },
};

export const DEFAULT_PREFS: ShellPrefs = { railOpen: true, pagesOpen: true };

export function shellMode(vw: number): ShellMode {
  if (vw >= BREAKPOINTS.wide) return "wide";
  if (vw >= BREAKPOINTS.desktop) return "desktop";
  if (vw >= BREAKPOINTS.compact) return "compact";
  if (vw >= BREAKPOINTS.narrow) return "narrow";
  return "single";
}

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));
const valid = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n) && n > 0;

export function shellLayout(viewportWidth: number, prefs: ShellPrefs = DEFAULT_PREFS, stored: StoredWidths = {}): ShellLayout {
  const vw = Math.max(0, Math.round(viewportWidth));
  const mode = shellMode(vw);
  const spec = MODES[mode];
  const saved = stored[mode] ?? {};

  // ── the rail ─────────────────────────────────────────────────────────────────────────────────
  // Docks only where the mode allows it AND the reader left it open; otherwise a strip, and below
  // `narrow` not even that — the header's menu control opens it as a drawer.
  let railKind: RailKind;
  let railW = 0;
  let railBounds: Bounds | undefined;
  if (spec.rail && prefs.railOpen) {
    railKind = "docked";
    // Leave room for the conversation's floor and a docked pages panel's minimum.
    const pagesFloor = spec.pages && prefs.pagesOpen ? spec.pages.min : EDGE_W;
    const max = Math.max(spec.rail.min, Math.min(spec.rail.max, vw - spec.chatMin - pagesFloor));
    railBounds = { min: spec.rail.min, max, def: clamp(spec.rail.def, spec.rail.min, max) };
    railW = clamp(valid(saved.rail) ? saved.rail : spec.rail.def, railBounds.min, railBounds.max);
  } else if (mode === "single") {
    railKind = "drawer";
  } else {
    railKind = "strip";
    railW = STRIP_W;
  }

  // ── the pages panel ──────────────────────────────────────────────────────────────────────────
  let pagesKind: PagesKind;
  let pagesW = 0;
  let pagesBounds: Bounds | undefined;
  if (spec.pages) {
    if (prefs.pagesOpen) {
      pagesKind = "docked";
      const fracCap = spec.pages.frac ? Math.round(vw * spec.pages.frac) : Infinity;
      const max = Math.max(spec.pages.min, Math.min(spec.pages.max, fracCap, vw - railW - spec.chatMin));
      pagesBounds = { min: spec.pages.min, max, def: clamp(spec.pages.def, spec.pages.min, max) };
      pagesW = clamp(valid(saved.pages) ? saved.pages : spec.pages.def, pagesBounds.min, pagesBounds.max);
    } else {
      pagesKind = "collapsed";
      pagesW = EDGE_W;
    }
  } else {
    pagesKind = mode === "single" ? "fullscreen" : "sheet";
  }

  const sheet = pagesKind === "fullscreen" ? vw : Math.min(SHEET_MAX, Math.round(vw * 0.9));
  const drawer = Math.min(DRAWER_W, Math.round(vw * 0.85));
  return {
    mode, railKind, pagesKind,
    columns: `${railW}px minmax(0, 1fr) ${pagesW}px`,
    widths: { rail: railW, pages: pagesW, chat: Math.max(0, vw - railW - pagesW), sheet, drawer },
    bounds: { ...(railBounds ? { rail: railBounds } : {}), ...(pagesBounds ? { pages: pagesBounds } : {}) },
    chatMin: spec.chatMin,
  };
}

/** Clamp a width a reader is dragging to, against the bounds the layout computed for that pane. */
export const clampWidth = (w: number, b: Bounds): number => clamp(Math.round(w), b.min, b.max);
