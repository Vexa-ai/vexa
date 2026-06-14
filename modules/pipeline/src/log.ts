// Host-injectable logger (the brick's only former coupling to the bot's utils).
type LogFn = (message: string, level?: string, ...rest: any[]) => void;
let _log: LogFn = (m, level) => console.log(level && level !== 'info' ? `[${level}] ${m}` : m);
export function setLogger(fn: LogFn): void { _log = fn; }
export const log: LogFn = (m, level, ...rest) => _log(m, level, ...rest);
