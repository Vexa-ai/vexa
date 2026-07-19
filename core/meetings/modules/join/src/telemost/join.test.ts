import { buildTelemostMeetingUrl } from "./join";

let failed = 0;
const equal = (name: string, actual: unknown, expected: unknown) => {
  const ok = actual === expected;
  console.log(`  ${ok ? "PASS" : "FAIL"} ${name}`);
  if (!ok) failed++;
};
const throws = (name: string, fn: () => unknown) => {
  try { fn(); console.log(`  FAIL ${name}`); failed++; }
  catch { console.log(`  PASS ${name}`); }
};

equal(
  "canonical URL preserved",
  buildTelemostMeetingUrl("https://telemost.yandex.ru/j/1111111111"),
  "https://telemost.yandex.ru/j/1111111111",
);
equal(
  "query parameters preserved",
  buildTelemostMeetingUrl("https://telemost.yandex.ru/j/1111111111?from=calendar"),
  "https://telemost.yandex.ru/j/1111111111?from=calendar",
);
throws("wrong host rejected", () => buildTelemostMeetingUrl("https://example.org/j/1111111111"));
throws("non-TLS rejected", () => buildTelemostMeetingUrl("http://telemost.yandex.ru/j/1111111111"));
throws("malformed id rejected", () => buildTelemostMeetingUrl("https://telemost.yandex.ru/j/abc"));

process.exit(failed ? 1 : 0);
