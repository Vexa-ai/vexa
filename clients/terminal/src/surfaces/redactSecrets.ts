/** redactSecrets — the browser-side half of P15.
 *
 *  `presentError` echoes every failure to the console and every surface renders its `detail`. On
 *  2026-09-02 that detail was `fatal: repository '<a GitHub PAT>' does not exist`, so the token was on
 *  screen AND in the console. The server scrubs its own text now; this is the second line, because a
 *  client cannot know which backend, proxy or fetch failure will hand it a string containing a secret
 *  — and because a browser console is a place people paste screenshots from.
 *
 *  Shape-based, mirroring `shared/git_redaction.py`: named token families, credentials wearing a URL,
 *  and a generic long opaque run — minus git object ids, which an operator reading a clone error needs. */
export const MASK = "«redacted»";

const TOKEN_FAMILIES = /\b(?:gh[pousr]_[A-Za-z0-9_]{4,}|github_pat_[A-Za-z0-9_]{4,}|glpat-[A-Za-z0-9_-]{4,})/g;
const URL_USERINFO = /(?<=:\/\/)[^/\s:@]+:[^/\s@]+(?=@)/g;
const URL_BARE_USERINFO = /(?<=:\/\/)[^/\s:@]{16,}(?=@)/g;
const GENERIC = /[A-Za-z0-9_-]{36,}/g;
const GIT_OID = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;
/** An SSH PUBLIC KEY is a long base64 run the generic rule would eat — and it is the opposite of a
 *  secret: it is the ANSWER shown when a credential is missing ("add this key to your repository").
 *  Masking it leaves a message reading "add this: «redacted»". */
const SSH_PUBKEY =
  /\b(?:ssh-(?:rsa|dss|ed25519)|ecdsa-sha2-[A-Za-z0-9-]+|sk-ssh-ed25519@openssh\.com)\s+[A-Za-z0-9+/]+=*(?:\s+\S+)?/g;

/** `text` with every credential-shaped run masked. Safe on anything, and safe to run twice. */
export function redactSecrets(text: unknown): string {
  let out = text === null || text === undefined ? "" : String(text);
  out = out.replace(TOKEN_FAMILIES, MASK);
  out = out.replace(URL_USERINFO, MASK);
  out = out.replace(URL_BARE_USERINFO, MASK);
  // Park public keys before the generic sweep and restore them after — the sweep cannot tell a key
  // from a secret by shape, and here that difference is the whole message.
  const kept: string[] = [];
  out = out.replace(SSH_PUBKEY, (m) => `\u0000pub${kept.push(m) - 1}\u0000`);
  out = out.replace(GENERIC, (m) => (GIT_OID.test(m) ? m : MASK));
  return kept.reduce((acc, original, i) => acc.replace(`\u0000pub${i}\u0000`, original), out);
}
