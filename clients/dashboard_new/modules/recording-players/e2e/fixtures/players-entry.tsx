/**
 * players-entry.tsx — the bundle entry the L4 fixture page loads.
 *
 * esbuild bundles THIS (and the REAL brick source it imports) into `players-bundle.js`, a single
 * browser-runnable ESM module. The point of the L4 harness is that the page mounts the SAME components
 * a human's browser would — not a re-implementation. So this file pulls AudioPlayer + VideoPlayer
 * straight from the brick SOURCE front door, and exposes a tiny `mountPlayers(target, props)` the page
 * calls to render them with the golden props.
 *
 * We import the brick by relative source path (../../src/...) on purpose: the brick's one front door is
 * src/index.ts. The @vexa/dash-contracts imports are type-only (erased at compile), so the bundle
 * carries no contract runtime — exactly the brick's real footprint.
 */
import { createElement } from "react";
import { createRoot } from "react-dom/client";
// Import the concrete brick source files (esbuild bundles TSX directly). The front door is
// src/index.ts; these are the two components it re-exports.
import { AudioPlayer } from "../../src/AudioPlayer.tsx";
import { VideoPlayer } from "../../src/VideoPlayer.tsx";

export function mountAudio(target, props) {
  createRoot(target).render(createElement(AudioPlayer, props));
}

export function mountVideo(target, props) {
  createRoot(target).render(createElement(VideoPlayer, props));
}
