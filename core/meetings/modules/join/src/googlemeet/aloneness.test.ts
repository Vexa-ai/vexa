/** L2: the alone-in-meeting watcher — the missing half of everyoneLeftTimeout.
 *  Continuous aloneness fires ONCE after the timeout; company returning resets the
 *  clock; a flaky counter read never causes a spurious leave. */
import { startGoogleAlonenessMonitor } from "./removal";
import { countRealParticipantTiles, countRealParticipantTilesOrThrow } from "./admission";

let failed = 0;
const check = (name: string, ok: boolean) => {
  console.log(`${ok ? "✓" : "✗"} ${name}`);
  if (!ok) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const fakePage = {} as never;

async function main() {
  // 1. alone the whole time → fires exactly once after the timeout
  let fires = 0;
  let stop = startGoogleAlonenessMonitor(fakePage, 60, () => { fires++; }, 20, async () => 1);
  await sleep(200);
  stop();
  check("continuous aloneness fires exactly once", fires === 1);

  // 2. company present → never fires
  fires = 0;
  stop = startGoogleAlonenessMonitor(fakePage, 60, () => { fires++; }, 20, async () => 2);
  await sleep(150);
  stop();
  check("never fires with company present", fires === 0);

  // 3. company returns mid-countdown → clock resets, no fire within the original window
  fires = 0;
  const seq = [1, 1, 2, 1, 1];
  let i = 0;
  stop = startGoogleAlonenessMonitor(fakePage, 80, () => { fires++; }, 20, async () => seq[Math.min(i++, seq.length - 1)]);
  await sleep(90);
  const firedEarly = fires;
  await sleep(120);
  stop();
  check("company returning resets the clock", firedEarly === 0 && fires === 1);

  // 4. counter throwing resets — never a spurious leave on flaky reads
  fires = 0;
  stop = startGoogleAlonenessMonitor(fakePage, 60, () => { fires++; }, 20, async () => { throw new Error("nav"); });
  await sleep(150);
  stop();
  check("flaky reads never fire", fires === 0);

  // 5. REGRESSION (the shipped false positive): check 4 above proves the MONITOR resets on
  //    a throw — but its DEFAULT counter swallowed every error and returned 0, which the
  //    monitor reads as "everyone left". One flaky DOM read could therefore evict the bot
  //    from a LIVE meeting. The two counters must differ exactly here: admission may treat
  //    a failed read as "no tiles yet" (0); aloneness must never.
  const brokenPage = { locator: () => ({ evaluateAll: async () => { throw new Error("detached frame"); } }) } as never;
  check("admission counter still degrades to 0 on a failed read", (await countRealParticipantTiles(brokenPage)) === 0);
  let surfaced = false;
  try { await countRealParticipantTilesOrThrow(brokenPage); } catch { surfaced = true; }
  check("aloneness counter surfaces a failed read instead of reporting 0", surfaced);

  if (failed) { console.error(`\n❌ aloneness (L2): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log("\n✅ aloneness (L2): all green.");
  process.exit(0);
}
main().catch((e) => { console.error(e); process.exit(1); });
