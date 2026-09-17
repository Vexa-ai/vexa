/**
 * Dial-in target parsing — `tel:` and `sip:` addresses for the `phone` platform.
 *
 * The Local room case: people are in a room with a conference speakerphone (Poly, Yealink, any
 * SIP endpoint) and there is no web meeting at all. The phone calls a number; the call IS the
 * meeting. There is no page, so nothing in this file touches Playwright — it is pure string
 * logic, kept beside the browser join flows because this is where the platform vocabulary lives.
 *
 * Feature-flagged: VEXA_PHONE_PLATFORM=1. With the flag unset every function here reports
 * "not a phone target" and the four browser platforms behave exactly as before.
 *
 * Identity, in two steps, because they are two different facts:
 *   • the ADDRESS — what you dial (`+15551234567`, `room-3@calls.example.org`, a DID + PIN).
 *     Stable, knowable before any call exists, minted by `parsePhoneTarget`.
 *   • the CALL — one conversation on that address. A DID hosts every call the room ever makes,
 *     so the address alone can never key a meeting: `phoneNativeMeetingId` appends the call
 *     discriminator (the trunk's Call-ID in production, the start instant offline).
 *
 * The SIP/RTP transport that would put real call audio behind this does not exist yet — see
 * `docs/adr/0036-dial-in-bridge-phone-platform.md` for what a live call still needs.
 */

/** Is the dial-in platform turned on for this process? Read at CALL time, so a test (and a
 *  reload) observes the live env rather than a value baked at import. */
export function phonePlatformEnabled(): boolean {
  const raw = (process.env.VEXA_PHONE_PLATFORM ?? '').trim().toLowerCase();
  return raw === '1' || raw === 'true';
}

/** A parsed dial-in address. `address` is the canonical form used as the meeting's address key. */
export interface PhoneTarget {
  scheme: 'tel' | 'sip';
  /** canonical dialable address: E.164 for tel, `user@host` for sip, `+…:PIN` when a PIN rides along */
  address: string;
  /** the E.164 number (tel) or the SIP user part */
  user: string;
  /** SIP host; absent for tel: */
  host?: string;
  /** conference PIN carried as a `;pin=` URI parameter, when the dialer supplies one */
  pin?: string;
}

// E.164 after visual separators are stripped: an optional +, then 4–15 digits.
const E164 = /^\+?\d{4,15}$/;
// A SIP user part: no separators, no whitespace, no scheme punctuation.
const SIP_USER = /^[^@\s:;/?#]+$/;
// A SIP host: a dotted name or an IPv4 literal. Deliberately conservative — a mangled URI
// must never yield a plausible-looking address.
const SIP_HOST = /^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$/;

/**
 * Parse a `tel:` or `sip:` URI into its dial-in address, or `null` when it is neither (or when
 * the dial-in platform is off).
 *
 *   tel:+1-555-123-4567            → { scheme: 'tel', address: '+15551234567' }
 *   tel:+15551234567;pin=482913    → { scheme: 'tel', address: '+15551234567:482913', pin: '482913' }
 *   sip:room-3@calls.example.org   → { scheme: 'sip', address: 'room-3@calls.example.org' }
 *
 * Visual separators (spaces, dashes, dots, parentheses) are stripped from a tel: number — the
 * RFC 3966 "visual-separator" set — so a number copied out of a room-booking page parses.
 */
export function parsePhoneTarget(raw: string): PhoneTarget | null {
  if (!phonePlatformEnabled()) return null;
  const value = (raw ?? '').trim();
  if (!value) return null;

  const colon = value.indexOf(':');
  if (colon < 0) return null;
  const scheme = value.slice(0, colon).toLowerCase();
  if (scheme !== 'tel' && scheme !== 'sip' && scheme !== 'sips') return null;

  // Split the URI parameters (`;key=value`) off the address part.
  const rest = value.slice(colon + 1);
  const [addressPart, ...paramParts] = rest.split(';');
  const params = new Map<string, string>();
  for (const p of paramParts) {
    const eq = p.indexOf('=');
    if (eq > 0) params.set(p.slice(0, eq).trim().toLowerCase(), p.slice(eq + 1).trim());
  }
  const pin = params.get('pin') || undefined;
  if (pin !== undefined && !/^\d{2,12}$/.test(pin)) return null;

  if (scheme === 'tel') {
    const number = addressPart.replace(/[\s().-]/g, '');
    if (!E164.test(number)) return null;
    const e164 = number.startsWith('+') ? number : `+${number}`;
    return { scheme: 'tel', address: pin ? `${e164}:${pin}` : e164, user: e164, pin };
  }

  // sip: / sips: — both ride the same address shape; the transport difference (TLS) is a trunk
  // concern, never an identity one, so a room does not change id when the trunk turns on TLS.
  const at = addressPart.lastIndexOf('@');
  if (at <= 0) return null;
  const user = addressPart.slice(0, at);
  const host = addressPart.slice(at + 1).toLowerCase();
  if (!SIP_USER.test(user) || !SIP_HOST.test(host) || !host.includes('.')) return null;
  const addr = `${user}@${host}`;
  return { scheme: 'sip', address: pin ? `${addr}:${pin}` : addr, user, host, pin };
}

/**
 * Mint the `native_meeting_id` for ONE call on a dial-in address.
 *
 * `discriminator` is the trunk's Call-ID once a real trunk exists (globally unique per RFC 3261,
 * which is exactly the property we need); offline it is the call's start instant, compacted to
 * `YYYYMMDDTHHMMSSZ`. Either way the address alone is never the id — a DID hosts every call the
 * room ever makes, and two calls that collided on one key would merge two meetings' transcripts.
 */
export function phoneNativeMeetingId(target: PhoneTarget, discriminator: string | Date): string {
  const d = discriminator instanceof Date
    ? discriminator.toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')
    : discriminator;
  if (!d) throw new Error('phoneNativeMeetingId: a call discriminator is required — an address alone cannot key a meeting');
  return `${target.address}:${d}`;
}
