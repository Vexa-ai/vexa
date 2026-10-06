export type JoinState = 'initializing' | 'authenticating' | 'connecting' | 'waiting_for_host' | 'waiting_room' | 'in_meeting' | 'reconnecting' | 'disconnecting' | 'ended';
export type FailureCode = 'invalid_config' | 'runtime_missing' | 'native_error' | 'authentication_failed' | 'join_failed' | 'protocol_error' | 'process_exit' | 'timeout' | 'cancelled' | 'left' | 'ended_before_admission';
export interface JoinConfig { meetingId: string; displayName: string; jwt: string; password?: string; onBehalfToken?: string; zak?: string; }
export type JoinEvent = { version: 1; kind: 'state'; state: JoinState } | { version: 1; kind: 'failure'; code: FailureCode; nativeCode?: number };
export interface JoinSession { admitted: Promise<void>; closed: Promise<void>; stop(): Promise<void>; }
