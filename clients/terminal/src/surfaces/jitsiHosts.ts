/** Client-side cache of the deployment's declared Jitsi hostnames (VEXA_JITSI_HOSTS), fetched
 *  once from /api/meeting/jitsi-hosts and passed into parseMeetingInput so a pasted link on a
 *  declared host parses exactly like it does server-side. Fetch failure → [] (the naming
 *  heuristics in the parser still apply). */

let cached: Promise<string[]> | null = null;

export function getJitsiHosts(): Promise<string[]> {
  if (!cached) {
    cached = fetch("/api/meeting/jitsi-hosts")
      .then((r) => (r.ok ? r.json() : { hosts: [] }))
      .then((j) => (Array.isArray(j?.hosts) ? j.hosts : []))
      .catch(() => []);
  }
  return cached;
}
