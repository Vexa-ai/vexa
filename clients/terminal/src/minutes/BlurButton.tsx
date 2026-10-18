"use client";
/** Background blur, one press per step: off → light → strong → off (founder 2026-10-15). On your own
 *  tile, before joining, and — as words — in the call bar's "…". */
import { useEffect } from "react";
import { Aperture } from "lucide-react";
import { BLUR_WORD, clearBlurNotice, cycleBlur, nextBlur, useBlur } from "./callBlur";

export function BlurButton() {
  const { level } = useBlur();
  const label = `Background blur: ${BLUR_WORD[level]} — press for ${BLUR_WORD[nextBlur(level)].toLowerCase()}`;
  return (
    <button type="button" className="vx-blur-btn" data-blur="" data-level={level} aria-label={label} title={label}
      onClick={(e) => { e.stopPropagation(); cycleBlur(); }}>
      <Aperture size={12} aria-hidden />{level === "off" ? "Blur" : BLUR_WORD[level]}
    </button>
  );
}

const NOTICE_MS = 8000;

/** The blur's one-line notice (unsupported, couldn't keep up) — under the call bar. */
export function BlurNotice() {
  const { notice } = useBlur();
  // SAID ONCE, IN THE CALL, AND GONE (founder 2026-10-16: it stood at the top of the home with no
  // call happening): it clears itself after a few seconds and when the call leaves the screen.
  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(clearBlurNotice, NOTICE_MS);
    return () => window.clearTimeout(t);
  }, [notice]);
  useEffect(() => () => clearBlurNotice(), []);
  return notice ? <div className="vx-cb-quiet" role="status" data-blur-notice="">{notice}</div> : null;
}
