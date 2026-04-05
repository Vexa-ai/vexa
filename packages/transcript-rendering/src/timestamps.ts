/**
 * Parse a timestamp string as UTC.
 *
 * Many transcription APIs return timestamps without timezone suffix
 * (e.g., "2025-12-11T14:20:25.222296") which JavaScript interprets as local time.
 * This function ensures UTC interpretation by appending 'Z' when no timezone is present.
 */
export function parseUTCTimestamp(timestamp: string): Date {
  const hasZone = /[zZ]$/.test(timestamp) || /[+-]\d{2}:\d{2}$/.test(timestamp);
  return new Date(hasZone ? timestamp : `${timestamp}Z`);
}
