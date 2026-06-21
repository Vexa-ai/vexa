/**
 * Desktop recording adapter — the recording.v1 finalize LIFECYCLE is OWNED by
 * `@vexa/recording` (`createRecordingAssembler` — accumulate → order-by-seq → drop the
 * empty final → `buildRecordingMaster`, finalized on `is_final` OR `close`). That
 * lifecycle is golden-validated in the module (`golden-lifecycle.test.ts`), including
 * the load-bearing close-without-is_final invariant — so the desktop no longer carries
 * its own copy (which is what let the Stop-race bug regress unseen).
 *
 * The desktop is the LOCAL receiver: it supplies only the disk/serve `onMaster` (in
 * `desktop.ts`, the composition root). This file re-exports the module assembler under
 * the desktop's port name so `desktop.ts` and its tests keep a stable local surface.
 * See ADR-0005.
 *
 * Direction: desktop (service) → `@vexa/recording` (a front door, P6). The brick never
 * imports the desktop.
 */
export { createRecordingAssembler as createRecordingSink } from '@vexa/recording';
export type {
  RecordingMaster,
  RecordingAssembler as RecordingSink,
  RecordingAssemblerOptions as RecordingSinkOptions,
} from '@vexa/recording';
