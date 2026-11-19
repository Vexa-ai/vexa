- **Git remotes, the signal model and wildcard model endpoints are checked by where they resolve.**
  - **Git remotes.** A workspace repository whose host resolves to a private, link-local (cloud
    metadata), CGNAT or loopback address is now refused. Before, only literal addresses and names
    without a dot were refused. git connects only to the addresses that were checked
    (`http.curloptResolve` for https, `ssh -o HostName` with `HostKeyAlias` for ssh), so a DNS
    record that changes after the check is not followed.
    - **If you clone from a git mirror inside your network,** list it in
      `VEXA_GIT_ALLOWED_INTERNAL_HOSTS` (Helm `agentApi.gitAllowedInternalHosts`) as exact host
      names or CIDRs.
    - A host already in `VEXA_GIT_PROVIDERS` needs no entry.
    - A malformed value stops agent-api at boot.
  - **The signal model.** A `base_url` set in `_global/POLICIES.md` for the signal model now goes
    through the outbound URL guard on every call: resolved, checked, and pinned to the checked
    address.
    - **An on-prem model server is reached only when listed** in `VEXA_SIGNALS_MODEL_ALLOW` (Helm
      `signals.modelAllow`).
    - A key travels over plain `http://` only to a host named there.
    - A refused endpoint is not called.
  - **Wildcard model endpoints.** A Settings → Models endpoint that `VEXA_MODEL_BASE_URL_ALLOW`
    admits only through a wildcard is resolved at dispatch. It is refused when it leads to a
    private, loopback or metadata address.
