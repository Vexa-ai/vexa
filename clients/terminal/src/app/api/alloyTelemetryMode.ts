/** ALLOY: Resolve the server-side telemetry opt-in at request time. */
export function isAlloySttTelemetryEnabled(
  raw = process.env.ALLOY_STT_TELEMETRY,
): boolean {
  return raw?.trim() === "1";
}
