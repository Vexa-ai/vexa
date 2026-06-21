// Host-injectable loggers (the brick's only former coupling to the bot's utils).
// logJSON is structured and injectable separately — the host's collectors
// depend on its exact stdout format, so the brick must not reshape it.
type LogFn = (message: string) => void;
type LogJSONFn = (obj: any) => void;
let _log: LogFn = (m) => console.log(m);
let _logJSON: LogJSONFn = (o) => console.log(JSON.stringify(o));
export function setLoggers(h: { log?: LogFn; logJSON?: LogJSONFn }): void {
  if (h.log) _log = h.log;
  if (h.logJSON) _logJSON = h.logJSON;
}
export const log: LogFn = (m) => _log(m);
export const logJSON: LogJSONFn = (o) => _logJSON(o);
