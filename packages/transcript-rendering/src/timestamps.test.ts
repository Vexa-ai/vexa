import { describe, it, expect } from 'vitest';
import { parseUTCTimestamp } from './timestamps';

describe('parseUTCTimestamp', () => {
  it('parses ISO timestamp with Z suffix', () => {
    const d = parseUTCTimestamp('2024-01-15T10:30:00Z');
    expect(d.getUTCFullYear()).toBe(2024);
    expect(d.getUTCMonth()).toBe(0); // January
    expect(d.getUTCDate()).toBe(15);
    expect(d.getUTCHours()).toBe(10);
    expect(d.getUTCMinutes()).toBe(30);
  });

  it('parses timestamp without timezone as UTC', () => {
    const d = parseUTCTimestamp('2025-12-11T14:20:25.222296');
    expect(d.getUTCHours()).toBe(14);
    expect(d.getUTCMinutes()).toBe(20);
  });

  it('parses timestamp with timezone offset', () => {
    const d = parseUTCTimestamp('2024-01-15T10:30:00+05:00');
    // 10:30 +05:00 = 05:30 UTC
    expect(d.getUTCHours()).toBe(5);
    expect(d.getUTCMinutes()).toBe(30);
  });

  it('returns Invalid Date for empty string', () => {
    const d = parseUTCTimestamp('');
    expect(isNaN(d.getTime())).toBe(true);
  });

  it('returns Invalid Date for invalid string', () => {
    const d = parseUTCTimestamp('not-a-date');
    expect(isNaN(d.getTime())).toBe(true);
  });

  it('handles lowercase z suffix', () => {
    const d = parseUTCTimestamp('2024-06-01T12:00:00z');
    expect(d.getUTCHours()).toBe(12);
  });
});
