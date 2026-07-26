/**
 * Shared L2 port doubles for constructing an orchestrator in tests.
 *
 * `createOrchestrator` requires `aloneness` (and the other core ports) unconditionally.
 * Keeping the noops in one place stops every new construction site from rediscovering that
 * by crashing with `Cannot read properties of undefined (reading "onAlone")`.
 */
import type { Act } from './contracts.js';
import type { ActsSource, AlonenessSource, Pipeline } from './ports.js';

/** Never-firing aloneness source — the default for suites that are not about left_alone. */
export const noopAloneness = (): AlonenessSource => ({
  onAlone() {
    return () => {
      /* */
    };
  },
});

/** Aloneness source the test can fire (and optionally observe stop). */
export const controlledAloneness = (
  ref: (fire: () => void) => void,
  onStop?: () => void,
): AlonenessSource => ({
  onAlone(callback) {
    ref(callback);
    return () => onStop?.();
  },
});

/** Start/stop-tracking pipeline double. */
export const noopPipeline = (): Pipeline & { started: boolean } => {
  const p = {
    started: false,
    async start() {
      p.started = true;
    },
    async stop() {
      p.started = false;
    },
  };
  return p;
};

/** Acts source; optional `ref` captures a fire(leave) handle for the test. */
export const noopActs = (ref?: (fire: (a: Act) => void) => void): ActsSource => ({
  subscribe(handler) {
    ref?.((a) => void handler(a));
    return () => {
      /* */
    };
  },
});
