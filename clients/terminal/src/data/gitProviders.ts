/** gitProviders — the git hosts this deployment can publish to, push to and clone from.
 *
 *  The server owns the list (`GET /api/workspace/git-providers`, agent-api's git provider port); the
 *  terminal only reads it. The built-in GitHub provider is always present, and is the default until
 *  the list has loaded — so this file is the ONE place in the terminal that names its host. Every
 *  other surface asks here: which provider a URL belongs to, its ssh form, how deep a path may be. */
import { getJson } from "./apiClient";

export type GitProviderKind = "github" | "gitlab" | "basic";

export interface GitProvider {
  name: string;
  kind: GitProviderKind;
  label: string;
  base_url: string;
  host: string;
  nested_groups: boolean;
  can_create: boolean;
  token_saved?: boolean;
  masked?: string | null;
}

export interface GitProviderList { default: string; providers: GitProvider[] }

/** The built-in provider, used before the list loads (and whenever it cannot). */
export const BUILTIN_GIT_PROVIDER: GitProvider = {
  name: "github", kind: "github", label: "GitHub", base_url: "https://github.com", host: "github.com",
  nested_groups: false, can_create: true,
};

export const BUILTIN_GIT_PROVIDERS: GitProviderList = { default: BUILTIN_GIT_PROVIDER.name, providers: [BUILTIN_GIT_PROVIDER] };

/** GitLab's group · 20 subgroups · project. */
const DEEPEST_PATH = 22;

/** GET the deployment's providers. A missing built-in is put back, so callers never special-case it. */
export async function listGitProviders(): Promise<GitProviderList> {
  const d = await getJson<Partial<GitProviderList>>(`/api/workspace/git-providers`);
  const providers = Array.isArray(d?.providers) ? d.providers : [];
  const list = providers.some((p) => p.name === BUILTIN_GIT_PROVIDER.name) ? providers : [BUILTIN_GIT_PROVIDER, ...providers];
  return { default: typeof d?.default === "string" && d.default ? d.default : BUILTIN_GIT_PROVIDER.name, providers: list };
}

// ── what the rest of the app last learned — synchronous callers (repoRef) read it ──────────────────
let known: GitProviderList = BUILTIN_GIT_PROVIDERS;

/** Remember the loaded list so synchronous checks (the repository field) use it. */
export function setKnownGitProviders(list: GitProviderList | GitProvider[] | null | undefined): void {
  if (!list) { known = BUILTIN_GIT_PROVIDERS; return; }
  if (Array.isArray(list)) { known = { default: BUILTIN_GIT_PROVIDER.name, providers: list.length ? list : [BUILTIN_GIT_PROVIDER] }; return; }
  known = list.providers.length ? list : BUILTIN_GIT_PROVIDERS;
}

export function knownGitProviders(): GitProviderList { return known; }

const asList = (providers?: GitProvider[] | GitProviderList | null): GitProvider[] =>
  !providers ? known.providers : Array.isArray(providers) ? providers : providers.providers;

/** The default provider of a list (or of the known list). */
export function defaultProvider(providers?: GitProvider[] | GitProviderList | null): GitProvider {
  const list = asList(providers);
  const name = providers && !Array.isArray(providers) ? providers.default : known.default;
  return list.find((p) => p.name === name) ?? list[0] ?? BUILTIN_GIT_PROVIDER;
}

/** host[:port] + base path of a provider, lower-cased; null when its base_url does not parse. */
function origin(p: GitProvider): { host: string; prefix: string } | null {
  try {
    const u = new URL(p.base_url);
    return { host: u.host.toLowerCase(), prefix: u.pathname.replace(/\/+$/, "") };
  } catch { return null; }
}

/** host[:port] and path of any repository URL form (https, ssh://, scp-like `user@host:path`). */
function locate(url: string): { host: string; path: string; scheme: "http" | "ssh" } | null {
  const v = (url ?? "").trim();
  const http = /^https?:\/\/(?:[^/@\s]+@)?([^/\s]+)(\/[^?#\s]*)?/i.exec(v);
  if (http) return { host: http[1].toLowerCase(), path: http[2] ?? "/", scheme: "http" };
  const ssh = /^ssh:\/\/(?:[^/@\s]+@)?([^/\s]+)(\/[^?#\s]*)?/i.exec(v);
  if (ssh) return { host: ssh[1].toLowerCase(), path: ssh[2] ?? "/", scheme: "ssh" };
  const scp = /^[A-Za-z0-9._-]+@([A-Za-z0-9.-]+):\/?([^?#\s]*)$/.exec(v);
  if (scp) return { host: scp[1].toLowerCase(), path: `/${scp[2]}`, scheme: "ssh" };
  return null;
}

const bareHost = (h: string) => h.toLowerCase().replace(/:\d{1,5}$/, "");

/** The configured provider a repository URL is on: same host[:port] AND under its base path. An ssh
 *  URL matches on the bare host (its port is the ssh port, not the web one). Longest prefix wins. */
export function providerForUrl(url: string | null | undefined, providers?: GitProvider[] | GitProviderList | null): GitProvider | null {
  const at = url ? locate(url) : null;
  if (!at) return null;
  let best: GitProvider | null = null;
  let bestLen = -1;
  for (const p of asList(providers)) {
    const o = origin(p);
    if (!o) continue;
    const sameHost = at.scheme === "http" ? at.host === o.host : bareHost(at.host) === bareHost(o.host) || bareHost(at.host) === bareHost(p.host || "");
    if (!sameHost) continue;
    const underPrefix = at.scheme === "ssh" || !o.prefix || at.path === o.prefix || at.path.startsWith(`${o.prefix}/`);
    if (underPrefix && o.prefix.length > bestLen) { best = p; bestLen = o.prefix.length; }
  }
  return best;
}

/** `git@<host>:<path>.git` for an https URL on a configured provider, else null. The base path is
 *  dropped (ssh addresses the repository from the host root). */
export function sshFormOf(httpsUrl: string, providers?: GitProvider[] | GitProviderList | null): string | null {
  const at = locate(httpsUrl);
  if (!at || at.scheme !== "http") return null;
  const p = providerForUrl(httpsUrl, providers);
  if (!p) return null;
  const o = origin(p);
  let path = at.path;
  if (o?.prefix && path.startsWith(`${o.prefix}/`)) path = path.slice(o.prefix.length);
  path = path.replace(/^\/+|\/+$/g, "").replace(/\.git$/, "");
  if (!path.includes("/")) return null;
  return `git@${bareHost(p.host || at.host)}:${path}.git`;
}

/** Deepest repository path accepted on a host: 2 on a GitHub-kind provider, GitLab's 22 elsewhere. */
export function maxPathSegments(host: string, providers?: GitProvider[] | GitProviderList | null): number {
  const h = bareHost(host);
  const p = asList(providers).find((x) => {
    const o = origin(x);
    return bareHost(x.host || "") === h || (o != null && bareHost(o.host) === h);
  });
  return p?.kind === "github" ? 2 : DEEPEST_PATH;
}

/** `owner/repo` shorthand → the full https URL on the default provider. */
export function shorthandUrl(owner: string, repo: string, providers?: GitProvider[] | GitProviderList | null): string {
  const base = defaultProvider(providers).base_url.replace(/\/+$/, "");
  return `${base}/${owner}/${repo}.git`;
}

/** The label to show for the provider a URL is on, or `fallback`. */
export function providerLabelForUrl(url: string | null | undefined, fallback: string, providers?: GitProvider[] | GitProviderList | null): string {
  return providerForUrl(url, providers)?.label ?? fallback;
}
