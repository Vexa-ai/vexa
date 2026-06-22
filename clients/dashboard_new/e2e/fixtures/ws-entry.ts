/**
 * ws-entry.ts — the bundle entry the fixture page loads.
 *
 * esbuild bundles THIS (and the real dash-ws source it imports) into `ws-bundle.js`, a single
 * browser-runnable ESM module. The point of the L4 harness is that the page runs the SAME brick code a
 * human's browser would — not a re-implementation. So this file does nothing but re-export the two
 * surfaces the fixture needs, pulled straight from the dash-ws brick SOURCE:
 *
 *   • `createWsClient`        — the real unified client (dash-ws/src/index.ts front door)
 *   • `createFakeWsTransport` — the deterministic transport (dash-ws/src/fakes.ts), so the page can
 *                               drive golden frames in with zero network. (When this harness graduates
 *                               to the real stack, swap this for a real WebSocket-backed WsTransport and
 *                               inject the goldens via redis — the page code above the transport is
 *                               unchanged.)
 *
 * We import the brick by relative source path (not `@vexa/dash-ws`) on purpose: the brick's one front
 * door is `src/index.ts`, and `createFakeWsTransport` is the test seam in `src/fakes.ts` — both are real
 * brick files. The `@vexa/dash-contracts` imports inside dash-ws are TYPE-ONLY (erased at compile), so
 * the bundle carries no contract runtime — exactly the brick's real footprint.
 */
import { createWsClient } from "../../modules/dash-ws/src/index.ts";
import { createFakeWsTransport } from "../../modules/dash-ws/src/fakes.ts";

export { createWsClient, createFakeWsTransport };
