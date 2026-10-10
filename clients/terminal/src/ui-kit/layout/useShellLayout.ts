"use client";
/** The shell layout's ONE writer (guidelines §3.1–3.2, P23): it measures the shell, keeps the
 *  reader's preferences and remembered widths under one `localStorage` key, and hands
 *  `shellLayout()` everything it needs. The shell renders whatever comes back; no surface reads the
 *  viewport width itself.
 *
 *  Three kinds of state, kept apart on purpose:
 *    · `prefs`   — what the reader asked for (rail open, pages open). Persisted. Changed only by the
 *                  reader's own toggles in a mode where the pane docks.
 *    · `widths`  — what the reader dragged each pane to, per mode. Persisted, clamped on read.
 *    · overlays  — whether the rail drawer or the pages sheet is open right now. Transient: they
 *                  close when the mode changes, and they never touch `prefs`. */
import { useCallback, useEffect, useLayoutEffect, useMemo, useState, type RefObject } from "react";
import { DEFAULT_PREFS, shellLayout, type Pane, type ShellLayout, type ShellMode, type ShellPrefs, type StoredWidths } from "./shellLayout";

export const SHELL_KEY = "vexa.shell.v1";

export type ShellStore = { prefs: ShellPrefs; widths: StoredWidths };

const MODES: ShellMode[] = ["wide", "desktop", "compact", "narrow", "single"];

/** Read the store, tolerating anything: a missing key, junk JSON, locked-down storage. A field of the
 *  wrong type is dropped rather than trusted; a width is kept whatever its size, because clamping is
 *  the layout's job and a width chosen on a wide monitor must come back as the widest this window
 *  allows, not as the default. */
export function readShellStore(fallback?: () => Partial<ShellStore> | null): ShellStore {
  let raw: string | null = null;
  try { raw = localStorage.getItem(SHELL_KEY); } catch { /* storage unavailable: defaults */ }
  if (raw === null) {
    const legacy = (() => { try { return fallback?.() ?? null; } catch { return null; } })();
    return { prefs: { ...DEFAULT_PREFS, ...(legacy?.prefs ?? {}) }, widths: legacy?.widths ?? {} };
  }
  let parsed: unknown = null;
  try { parsed = JSON.parse(raw); } catch { /* junk: defaults */ }
  const o = (parsed && typeof parsed === "object" ? parsed : {}) as Record<string, unknown>;
  const p = (o.prefs && typeof o.prefs === "object" ? o.prefs : {}) as Record<string, unknown>;
  const prefs: ShellPrefs = {
    railOpen: typeof p.railOpen === "boolean" ? p.railOpen : DEFAULT_PREFS.railOpen,
    pagesOpen: typeof p.pagesOpen === "boolean" ? p.pagesOpen : DEFAULT_PREFS.pagesOpen,
  };
  const w = (o.widths && typeof o.widths === "object" ? o.widths : {}) as Record<string, unknown>;
  const widths: StoredWidths = {};
  for (const m of MODES) {
    const e = w[m] as Record<string, unknown> | undefined;
    if (!e || typeof e !== "object") continue;
    const out: Partial<Record<Pane, number>> = {};
    for (const pane of ["rail", "pages"] as Pane[]) {
      const n = e[pane];
      if (typeof n === "number" && Number.isFinite(n) && n > 0) out[pane] = Math.round(n);
    }
    if (Object.keys(out).length) widths[m] = out;
  }
  return { prefs, widths };
}

export function writeShellStore(s: ShellStore): void {
  try { localStorage.setItem(SHELL_KEY, JSON.stringify(s)); } catch { /* storage unavailable: this session only */ }
}

export type ShellControls = {
  layout: ShellLayout;
  prefs: ShellPrefs;
  drawerOpen: boolean;
  sheetOpen: boolean;
  /** The reader's rail control: docks/undocks where the rail may dock, else opens/closes the drawer. */
  toggleRail: () => void;
  setRailOpen: (open: boolean) => void;
  /** The reader's pages control: shows/hides a docked panel, else opens/closes the sheet. */
  setPagesOpen: (open: boolean) => void;
  /** A document was opened. A docked-mode panel unfolds (a link must never open into a hidden
   *  column). In overlay modes the sheet opens only when the READER asked (`byReader`) — the agent
   *  writing a page must not cover the conversation the reader is typing into. */
  revealPages: (byReader: boolean) => void;
  closeOverlays: () => void;
  /** Live drag: render the new width without persisting it. */
  previewWidth: (pane: Pane, w: number) => void;
  /** End of a drag / a key press / a reset: render AND remember, for this mode only. */
  commitWidth: (pane: Pane, w: number | null) => void;
};

/** `rootRef` is the shell's own box; its width is the viewport the layout is computed for. */
export function useShellLayout(rootRef: RefObject<HTMLElement | null>, opts: { legacy?: () => Partial<ShellStore> | null } = {}): ShellControls {
  const [store, setStore] = useState<ShellStore>(() => readShellStore(opts.legacy));
  const [vw, setVw] = useState<number>(() => (typeof window === "undefined" ? 1440 : window.innerWidth));
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [live, setLive] = useState<Partial<Record<Pane, number>>>({});

  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const measure = () => { const w = el.getBoundingClientRect().width; if (w > 0) setVw(Math.round(w)); };
    measure();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [rootRef]);

  const stored = useMemo<StoredWidths>(() => {
    const mode = shellLayout(vw, store.prefs, store.widths).mode;
    if (!live.rail && !live.pages) return store.widths;
    return { ...store.widths, [mode]: { ...(store.widths[mode] ?? {}), ...live } };
  }, [store, live, vw]);
  const layout = useMemo(() => shellLayout(vw, store.prefs, stored), [vw, store.prefs, stored]);

  // Overlays belong to the mode they were opened in.
  useEffect(() => { setDrawerOpen(false); setSheetOpen(false); setLive({}); }, [layout.mode]);
  // A drawer has nothing to hold once the rail docks; nor a sheet once the panel docks.
  useEffect(() => { if (layout.railKind === "docked") setDrawerOpen(false); }, [layout.railKind]);

  const update = useCallback((fn: (s: ShellStore) => ShellStore) => {
    setStore((s) => { const n = fn(s); if (n !== s) writeShellStore(n); return n; });
  }, []);

  const railDocks = layout.mode === "wide" || layout.mode === "desktop";
  const pagesDock = layout.pagesKind === "docked" || layout.pagesKind === "collapsed";

  const setRailOpen = useCallback((open: boolean) => {
    if (railDocks) update((s) => (s.prefs.railOpen === open ? s : { ...s, prefs: { ...s.prefs, railOpen: open } }));
    else setDrawerOpen(open);
  }, [railDocks, update]);
  const toggleRail = useCallback(() => {
    if (railDocks) update((s) => ({ ...s, prefs: { ...s.prefs, railOpen: !s.prefs.railOpen } }));
    else setDrawerOpen((v) => !v);
  }, [railDocks, update]);
  const setPagesOpen = useCallback((open: boolean) => {
    if (pagesDock) update((s) => (s.prefs.pagesOpen === open ? s : { ...s, prefs: { ...s.prefs, pagesOpen: open } }));
    else setSheetOpen(open);
  }, [pagesDock, update]);
  const revealPages = useCallback((byReader: boolean) => {
    if (pagesDock) update((s) => (s.prefs.pagesOpen ? s : { ...s, prefs: { ...s.prefs, pagesOpen: true } }));
    else if (byReader) setSheetOpen(true);
  }, [pagesDock, update]);
  const closeOverlays = useCallback(() => { setDrawerOpen(false); setSheetOpen(false); }, []);

  const previewWidth = useCallback((pane: Pane, w: number) => setLive((l) => ({ ...l, [pane]: Math.round(w) })), []);
  const commitWidth = useCallback((pane: Pane, w: number | null) => {
    setLive((l) => { const n = { ...l }; delete n[pane]; return n; });
    const mode = layout.mode;
    update((s) => {
      const cur = { ...(s.widths[mode] ?? {}) };
      if (w === null) delete cur[pane]; else cur[pane] = Math.round(w);
      return { ...s, widths: { ...s.widths, [mode]: cur } };
    });
  }, [layout.mode, update]);

  return {
    layout, prefs: store.prefs, drawerOpen: drawerOpen && layout.railKind !== "docked",
    sheetOpen: sheetOpen && (layout.pagesKind === "sheet" || layout.pagesKind === "fullscreen"),
    toggleRail, setRailOpen, setPagesOpen, revealPages, closeOverlays, previewWidth, commitWidth,
  };
}
