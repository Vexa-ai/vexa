// ensureGuestName: a guest bot never knocks with an empty or wrong name (prod 38828, 17 Nov 2026).
import { ensureGuestName, GOOGLE_NAME_NOT_SET } from "./join";

let failures = 0;
const check = (what: string, ok: boolean) => { console.log(`${ok ? "PASS" : "FAIL"} ${what}`); if (!ok) failures++; };

async function main() {
  let refills = 0;
  await ensureGuestName(async () => "Vexa", async () => { refills++; }, "Vexa");
  check("a field holding the name passes with no refill", refills === 0);

  let value = "";
  await ensureGuestName(async () => value, async () => { value = "Vexa"; refills++; }, "Vexa");
  check("an empty field is refilled once and passes", refills === 1 && value === "Vexa");

  let err: any = null;
  try { await ensureGuestName(async () => "", async () => {}, "Vexa"); } catch (e) { err = e; }
  check("a field that stays empty fails typed, before any knock", !!err && String(err.message).startsWith(GOOGLE_NAME_NOT_SET));

  err = null;
  try { await ensureGuestName(async () => "Anonymous", async () => { throw new Error("detached"); }, "Vexa"); } catch (e) { err = e; }
  check("a wrong name with a failing refill fails typed", !!err && String(err.message).includes("Anonymous"));

  err = null;
  try { await ensureGuestName(async () => "", async () => {}, "  "); } catch (e) { err = e; }
  check("no bot name fails typed", !!err && String(err.message).startsWith(GOOGLE_NAME_NOT_SET));

  if (failures) { console.error(`${failures} failed`); process.exit(1); }
  console.log("guest-name: all passed");
}
main();
