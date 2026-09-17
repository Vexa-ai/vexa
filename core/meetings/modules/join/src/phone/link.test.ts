/**
 * L2 — dial-in address parsing + per-call identity, and the feature flag's negative control.
 *
 * Pins three things:
 *   • with VEXA_PHONE_PLATFORM unset, NOTHING about the four browser platforms changes and no
 *     tel:/sip: URI is recognized (the flag is the whole blast radius);
 *   • with the flag on, tel:/sip: resolve to the `phone` platform and canonicalize;
 *   • `joinMeeting` REFUSES phone before touching the page — a dial-in call has no page.
 *
 * Run: npx tsx core/meetings/modules/join/src/phone/link.test.ts
 */
import { parsePhoneTarget, phoneNativeMeetingId, phonePlatformEnabled } from './link';
import { joinMeeting, resolvePlatform } from '../index';

let passed = 0;
let failed = 0;
function check(name: string, ok: boolean, detail?: string) {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ''}`); failed++; }
}
const threw = (fn: () => unknown): string | null => { try { fn(); return null; } catch (e: any) { return e?.message ?? 'threw'; } };

// A page that fails LOUD if any join flow touches it.
const explodingPage: any = new Proxy({}, {
  get: (_t, prop) => {
    if (prop === 'then') return undefined;
    return () => { throw new Error(`page.${String(prop)} was called — a join flow RAN`); };
  },
});

async function main() {
  // ── 1) flag OFF (the negative control) ──────────────────────────────────────────────
  delete process.env.VEXA_PHONE_PLATFORM;
  check('flag unset ⇒ phonePlatformEnabled() is false', phonePlatformEnabled() === false);
  check('flag unset ⇒ tel: is not a phone target', parsePhoneTarget('tel:+15551234567') === null);
  check('flag unset ⇒ sip: is not a phone target', parsePhoneTarget('sip:room-3@calls.example.org') === null);
  check('flag unset ⇒ tel: cannot be resolved to any platform (throws, as before)',
    /Cannot infer platform/.test(threw(() => resolvePlatform('tel:+15551234567')) ?? ''));
  check('flag unset ⇒ google meet still resolves', resolvePlatform('https://meet.google.com/abc-defg-hij') === 'google_meet');
  check('flag unset ⇒ zoom still resolves', resolvePlatform('https://zoom.us/j/12345678901') === 'zoom');
  check('flag unset ⇒ teams still resolves', resolvePlatform('https://teams.microsoft.com/l/meetup-join/x') === 'teams');
  check('flag unset ⇒ jitsi still resolves', resolvePlatform('https://meet.jit.si/daily') === 'jitsi');

  // ── 2) flag ON ──────────────────────────────────────────────────────────────────────
  process.env.VEXA_PHONE_PLATFORM = '1';
  check('flag on ⇒ phonePlatformEnabled() is true', phonePlatformEnabled() === true);

  check('tel: resolves to the phone platform', resolvePlatform('tel:+15551234567') === 'phone');
  check('sip: resolves to the phone platform', resolvePlatform('sip:room-3@calls.example.org') === 'phone');
  check('sips: resolves to the phone platform', resolvePlatform('sips:room-3@calls.example.org') === 'phone');

  const t = parsePhoneTarget('tel:+1 (555) 123-4567');
  check('tel: strips visual separators to E.164', t?.address === '+15551234567', JSON.stringify(t));
  const noPlus = parsePhoneTarget('tel:15551234567');
  check('tel: without a leading + is canonicalized to E.164', noPlus?.address === '+15551234567', JSON.stringify(noPlus));

  const withPin = parsePhoneTarget('tel:+15551234567;pin=482913');
  check('a ;pin= parameter rides into the address', withPin?.address === '+15551234567:482913', JSON.stringify(withPin));
  check('the pin is also exposed on its own', withPin?.pin === '482913', JSON.stringify(withPin));

  const s = parsePhoneTarget('sip:Room-3@Calls.Example.ORG');
  check('sip: keeps the user part and lowercases the host', s?.address === 'Room-3@calls.example.org', JSON.stringify(s));
  check('sip: exposes user + host', s?.user === 'Room-3' && s?.host === 'calls.example.org', JSON.stringify(s));

  // Refusals — a mangled URI must never yield a plausible-looking address.
  check('a bare number is not a dial-in URI', parsePhoneTarget('+15551234567') === null);
  check('an http URL is not a dial-in URI', parsePhoneTarget('https://meet.google.com/abc-defg-hij') === null);
  check('tel: with too few digits is refused', parsePhoneTarget('tel:+12') === null);
  check('tel: with letters is refused', parsePhoneTarget('tel:+1555CALLME') === null);
  check('sip: with no host is refused', parsePhoneTarget('sip:room-3') === null);
  check('sip: with a dotless host is refused', parsePhoneTarget('sip:room-3@localhost') === null);
  check('a non-numeric pin is refused', parsePhoneTarget('tel:+15551234567;pin=abc') === null);

  // ── 3) per-call identity ────────────────────────────────────────────────────────────
  const target = parsePhoneTarget('tel:+15551234567')!;
  check('a Date discriminator compacts to YYYYMMDDTHHMMSSZ',
    phoneNativeMeetingId(target, new Date('2026-09-17T14:30:00.000Z')) === '+15551234567:20260917T143000Z',
    phoneNativeMeetingId(target, new Date('2026-09-17T14:30:00.000Z')));
  check('a trunk Call-ID discriminator is used verbatim',
    phoneNativeMeetingId(target, 'a84b4c76e66710') === '+15551234567:a84b4c76e66710');
  check('two calls on the same address get different ids',
    phoneNativeMeetingId(target, new Date('2026-09-17T14:30:00Z')) !== phoneNativeMeetingId(target, new Date('2026-09-17T15:00:00Z')));
  check('an empty discriminator is refused (an address alone cannot key a meeting)',
    /cannot key a meeting/.test(threw(() => phoneNativeMeetingId(target, '')) ?? ''));

  // ── 4) the join layer refuses phone before touching the page ────────────────────────
  try {
    await joinMeeting(explodingPage, { meetingUrl: 'tel:+15551234567' });
    check('joinMeeting refuses the phone platform', false, 'joinMeeting resolved');
  } catch (e: any) {
    check('joinMeeting refuses phone with a typed message, before any page interaction',
      /Platform 'phone' does not join through a browser page/.test(e.message), e.message);
  }

  delete process.env.VEXA_PHONE_PLATFORM;
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
