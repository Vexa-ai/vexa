/** session-diagnostic — named Zoom sign-in cookies, lifetimes and flags, never a value. Run: npx tsx src/zoom/session-diagnostic.test.ts */
import { describeZoomSignInCookies, cookieLifetime } from "./session-diagnostic";

let failed = 0;
const check = (name: string, ok: boolean, detail = "") => { console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : "  — " + detail}`); if (!ok) failed++; };

const NOW = 1_000_000;
const SECRET = "s3cr3t-VALUE-9f3k";
const cookies: any[] = [
  { name: "_zm_ssid", domain: ".zoom.us", value: SECRET, expires: -1, httpOnly: true, secure: true },
  { name: "_zm_ssid", domain: "us05web.zoom.us", value: "abc", expires: NOW + 3 * 86400 + 5, httpOnly: false, secure: true },
  { name: "zm_aid", domain: ".zoom.us", value: SECRET, expires: NOW - 10, httpOnly: true, secure: true },
  { name: "zm_haid", domain: ".zoom.us", value: SECRET, expires: NOW + 7200 },
  { name: "mySSOtoken", domain: ".zoom.us", value: SECRET, expires: 0 },
  { name: "unrelated", domain: ".zoom.us", value: SECRET, expires: -1 },
  { name: "_zm_ssid", domain: "zoom.us.attacker.example", value: SECRET, expires: -1 },
  { name: "SID", domain: ".google.com", value: SECRET },
  { name: "cred=" + SECRET, domain: ".zoom.com", value: SECRET, expires: -1 },
];
const line = describeZoomSignInCookies(cookies, NOW);
console.log("  line:", line);
check("never a value", !line.includes(SECRET) && !line.includes("abc,"), line);
check("session-only is said exactly", line.includes("_zm_ssid@.zoom.us: session-only, valueLength=17, httpOnly=true, secure=true"), line);
check("expiring in days", line.includes("_zm_ssid@us05web.zoom.us: expires in 3d, valueLength=3, httpOnly=false, secure=true"), line);
check("expiring in hours", line.includes("zm_haid@.zoom.us: expires in 2h"), line);
check("expired", line.includes("zm_aid@.zoom.us: EXPIRED"), line);
check("a name matching /sso/i counts", line.includes("mySSOtoken@.zoom.us: session-only"), line);
check("a non-candidate is not named", !line.includes("unrelated@"), line);
check("multiple domains for one name", (line.match(/_zm_ssid@/g) || []).length === 2, line);
check("non-Zoom domains ignored", !line.includes("attacker") && !line.includes("google") && !line.includes("SID@"), line);
check("absent names listed once", /absent: _zm_kms zm_jwt cred _zm_page_auth zm_cluster;/.test(line), line);
check("totals per Zoom domain with session-only counts",
  line.includes(".zoom.us 5 (session-only 3)") && line.includes("us05web.zoom.us 1 (session-only 0)") && line.includes(".zoom.com 1 (session-only 1)"), line);
check("lifetime: absent expires is session-only", cookieLifetime(undefined, NOW) === "session-only");
check("empty jar", describeZoomSignInCookies([], NOW).includes(": none; absent: _zm_ssid zm_aid"));

if (failed) { console.error(`\n${failed} check(s) failed`); process.exit(1); }
console.log("\nsession-diagnostic: all checks passed");
