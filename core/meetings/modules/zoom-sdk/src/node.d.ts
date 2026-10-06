import type { JoinConfig, JoinEvent, JoinSession, FailureCode } from './index.js';
export class JoinError extends Error { code: FailureCode; }
export function joinSdk(config: JoinConfig, options: { sdkDir: string; addonPath: string; onState?: (event: JoinEvent) => void; signal?: AbortSignal; timeoutMs?: number; cleanupTimeoutMs?: number }): JoinSession;
