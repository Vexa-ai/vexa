import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Parse a timestamp string as UTC.
 * The API returns timestamps without timezone suffix (e.g., "2025-12-11T14:20:25.222296")
 * which JavaScript would interpret as local time. This function ensures UTC interpretation.
 */
export function parseUTCTimestamp(timestamp: string): Date {
  // If timestamp already includes timezone info (Z or +HH:MM/-HH:MM), do not modify it.
  // Otherwise, append 'Z' so JS interprets it as UTC instead of local time.
  const hasZone =
    /[zZ]$/.test(timestamp) || /[+-]\d{2}:\d{2}$/.test(timestamp);
  return new Date(hasZone ? timestamp : `${timestamp}Z`);
}
