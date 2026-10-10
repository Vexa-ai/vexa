/** The emailed sign-in link's DELIVERY — admit, then send — run after the request has answered.
 *
 *  `request-link/route.ts` answers every well-formed address with the same 200 before any of this
 *  runs (Vexa-ai/vexa#1783). If the admission question and the SMTP send ran first, a refused
 *  address would come back faster than an allowed one by the length of a mail round-trip, and the
 *  form would reveal who may sign in through its timing even though its words never do.
 *
 *  Lives outside the route file because an App Router `route.ts` may export only HTTP handlers and
 *  route config, and the test seam below is neither.
 */
import { sendMail } from "./mailer";
import { signinAdmission } from "./adminApi";

/** Deliveries still running after their response went out. The terminal is a long-lived Node
 *  server (server.mjs), so a promise outlives the request that started it; the set exists so a test
 *  can wait for one, and so nothing here is fire-and-forget in the sense of unobservable. */
const inFlight = new Set<Promise<void>>();

/** Ask, then send — never the other way round, and nothing at all for an address that was not
 *  admitted (or while admission cannot be decided: fail closed). Every outcome is logged here and
 *  none reaches the person who asked. */
async function deliver(to: string, url: string, minutes: number): Promise<void> {
  const admission = await signinAdmission(to);
  if (!admission.admitted) {
    if (admission.why === "unavailable") {
      console.error(`[terminal-auth] sign-in link NOT sent — admission unavailable (fail closed): ${admission.detail ?? ""}`);
    } else {
      console.info(`[terminal-auth] sign-in link NOT sent — ${to} is not allowed to sign in`);
    }
    return;
  }
  try {
    await sendMail({
      to,
      subject: "Your Vexa sign-in link",
      text: `Sign in to Vexa:\n\n${url}\n\nThe link works once and expires in ${minutes} minutes.\n`,
    });
  } catch (err) {
    // Never surfaced: the caller must not learn whether the address exists OR whether mail works.
    console.error(`[terminal-auth] sign-in link delivery failed: ${(err as Error).message}`);
  }
}

/** Start one delivery and return at once. Never throws, never rejects. */
export function startLinkDelivery(to: string, url: string, minutes: number): void {
  const work = deliver(to, url, minutes).catch((err) => {
    console.error(`[terminal-auth] sign-in link delivery threw: ${(err as Error).message}`);
  });
  inFlight.add(work);
  void work.finally(() => inFlight.delete(work));
}

/** Test seam: resolves once every delivery started so far has finished. */
export async function _settleLinkDeliveries(): Promise<void> {
  while (inFlight.size) await Promise.all([...inFlight]);
}
