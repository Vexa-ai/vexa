/** repoRef — what may be typed into the attach dialog's "Repository" field.
 *
 *  The twin of `control_plane/repo_ref.py`, and deliberately a twin rather than a round trip: on
 *  2026-09-02 a GitHub PAT was pasted into that field and SENT, and git's reply — `fatal: repository
 *  '<the token>' does not exist` — put the secret in the error card and the browser console. The
 *  server refuses it now too (422, before any git process exists), but the client must refuse FIRST,
 *  because the fix is not "show a nicer error": it is that **the value never leaves the tab**.
 *
 *  A whitelist, not a blacklist. The set of things that are a repository is small and knowable; the
 *  set of things that are a secret is neither. */

/** Token families we can name — the prefix check is the cheap half; anything long and opaque that
 *  isn't a repository shape is refused by the whitelist anyway. */
const TOKEN_PREFIX = /^(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|glpat-)/;

/** The one sentence a person sees when they paste a credential where a repository goes. It names what
 *  they did, what to do instead, and where the other thing lives — a refusal that only says "invalid"
 *  teaches nothing and gets retried verbatim. Identical to the server's, so a client-side refusal and
 *  a server-side one are the same experience. */
export const TOKEN_SENTENCE =
  "That looks like a token, not a repository. Paste the repository URL here; a saved token goes in the token card.";
export const SHAPE_SENTENCE =
  "That is not a repository. Use https://github.com/owner/repo, git@github.com:owner/repo.git, owner/repo, " +
  "or a full URL with groups (https://git.example.com/group/subgroup/repo).";

/** The deepest repository path accepted — GitLab's group · 20 subgroups · project. `github.com` has
 *  exactly two segments, so a deeper path there is refused rather than cloned and failed. */
export const MAX_PATH_SEGMENTS = 22;

const SEG = "[A-Za-z0-9._-]+";
const SEG_RE = new RegExp(`^${SEG}$`);
const HOST = "[A-Za-z0-9.-]+(?::\\d{1,5})?";
const PATH = "([^?#\\s]+?)";
const HTTP = new RegExp(`^(https?)://(${HOST})/${PATH}/?$`);
const SCP = new RegExp(`^([A-Za-z0-9._-]+)@([A-Za-z0-9.-]+):/?${PATH}/?$`);
const SSH = new RegExp(`^ssh://(?:([A-Za-z0-9._-]+)@)?(${HOST})/${PATH}/?$`);
const BARE = new RegExp(`^(${SEG})/(${SEG})$`);
const USERINFO = /^[a-z]+:\/\/([^/\s@]+)@/;

const tidy = (name: string) => name.replace(/\.git$/, "");

/** `owner/repo` or `group/sub/…/repo`, `.git` dropped — or null. Every segment is a plain name: never
 *  empty, `.`, `..` or GitLab's `-` route separator, so a path cannot climb out of where it points. */
function repoPath(host: string, path: string): string | null {
  const segs = path.split("/");
  segs[segs.length - 1] = tidy(segs[segs.length - 1]);
  const bare = host.toLowerCase().replace(/:\d{1,5}$/, "");
  const limit = bare === "github.com" ? 2 : MAX_PATH_SEGMENTS;
  if (segs.length < 2 || segs.length > limit) return null;
  for (const seg of segs) {
    if (!SEG_RE.test(seg) || /^\.+$/.test(seg) || seg === "-") return null;
  }
  return segs.join("/");
}

export type RepoCheck =
  | { ok: true; url: string }
  | { ok: false; kind: "token" | "shape"; sentence: string };

/** True when the value is credential-shaped ON ITS OWN — checked before anything else happens to it. */
export function looksLikeToken(value: string): boolean {
  return TOKEN_PREFIX.test(value.trim());
}

/** Validate + canonicalize what the person typed. `""` is not an error — an empty field is simply not
 *  yet a repository, and the submit button is what gates that. */
export function checkRepo(raw: string): RepoCheck {
  const v = (raw ?? "").trim();
  if (!v) return { ok: false, kind: "shape", sentence: SHAPE_SENTENCE };
  if (looksLikeToken(v)) return { ok: false, kind: "token", sentence: TOKEN_SENTENCE };

  // git's own "authenticated clone URL" carries a credential as plain USERINFO, which is how a PAT
  // most often arrives here — pasted inside a URL copied out of a tutorial. `ssh://git@host/…` is the
  // legitimate case and must survive, so the discriminator is the userinfo itself: a user:password
  // pair, a token-shaped run, or anything long enough to be opaque. A real ssh user is none of those.
  const ui = USERINFO.exec(v);
  if (ui && (ui[1].includes(":") || looksLikeToken(ui[1]) || ui[1].length >= 20)) {
    return { ok: false, kind: "token", sentence: TOKEN_SENTENCE };
  }

  const shape = { ok: false as const, kind: "shape" as const, sentence: SHAPE_SENTENCE };
  let m = HTTP.exec(v);
  if (m) {
    const p = repoPath(m[2], m[3]);
    return p ? { ok: true, url: `${m[1]}://${m[2]}/${p}.git` } : shape;
  }
  m = SCP.exec(v);
  if (m) {
    const p = repoPath(m[2], m[3]);
    return p ? { ok: true, url: `${m[1]}@${m[2]}:${p}.git` } : shape;
  }
  m = SSH.exec(v);
  if (m) {
    const p = repoPath(m[2], m[3]);
    return p ? { ok: true, url: `ssh://${m[1] ? `${m[1]}@` : ""}${m[2]}/${p}.git` } : shape;
  }
  m = BARE.exec(v);
  if (m) return { ok: true, url: `https://github.com/${m[1]}/${tidy(m[2])}.git` };
  return { ok: false, kind: "shape", sentence: SHAPE_SENTENCE };
}
