# Blue/green on the dogfood stack — how a swap stopped needing a person

PRD decision 39, founder 2026-09-02: *"what's this out / in? why we need this?"*

The ritual existed for two reasons, and both were ours:

| | what it was | why the person was involved |
|---|---|---|
| **F20** | a container recreated under an open tab fails the request in flight | the founder saw `fetch failed` mid-stream and read it as the product breaking |
| **F55 / F77** | the terminal and the server must move together | a new terminal on an old server lost a tab; a terminal built ahead of the server shipped a button every running agent-api answered 422 |

Neither needs a human. `deploy.sh` starts the new container **beside** the old, proves it before it
can be reached, switches traffic with no gap, and keeps the old one running for one `rollback.sh`
step. `GET /api/version` + the terminal's reload bar (`Vexa-ai/vexa`, `clients/terminal`) close the
last gap: a tab that was open across a swap says so, and reloads only if the person clicks.

## Use

```bash
deploy.sh --check line-6bec34db4      # every guard against the real target; changes nothing
deploy.sh line-6bec34db4              # agent-api → runtime → terminal
deploy.sh agent-api=line-a,terminal=line-b   # per service
rollback.sh [service]                 # one step back; terminal first
deploy.sh --retire                    # drop the `-prev` containers (rollback no longer possible)
```

The terminal image is `<tag>-minutes` (`BG_TERMINAL_SUFFIX`). Every default in `bg-lib.sh` points
at the founder's live stack and every one of them is overridable, which is how the whole thing was
rehearsed on a throwaway project without touching `vexa-dogfood`.

## The two switches, and why there are two

A service is reached two different ways, and only one of them is docker's DNS.

* **In-network** — `agent-api`, `runtime`. The new container is reconnected carrying the service
  alias while the old one still holds it, so the name never resolves to nothing; the old one is
  taken off only after the new one has answered on it.
* **From the host** — `app.dev.vexa.ai`, and the flows lane's `localhost:18500`. An nginx upstream
  is rewritten and reloaded. nginx finishes in-flight requests on its old worker processes, which
  is why the reload costs nothing.

Both, per service, in this order: start beside → prove both sides → alias → nginx → release the old
alias → rename `-prev`. Then the terminal, last, only after the pairing guard has asked the
agent-api **that is actually serving** what contract it answers.

## What the rehearsal found (2026-09-02, `-p vexa-bg-test`, since removed)

Four defects, each of which would have been a live incident:

1. **`docker network disconnect` kills the container's published host port.** The outgoing
   container was left on no network at all, docker tore down its proxy, and the stable port
   answered **502 — 127 times in one swap**, caught by the request loop. Fixed by reconnecting it
   under its own name: it keeps running, keeps its binding, and stays one command from serving.
2. **The reconnect re-publishes on a DIFFERENT ephemeral port.** An upstream written before it
   points at a port nobody is listening on. The port is now re-read and re-probed after the switch.
3. **`local svc="$1" port="${BG_PORT[$svc]}"` reads the CALLER's `svc`.** bash expands every
   assignment word in a `local` against the outer scope. The server loop hid it perfectly — the
   caller's `svc` happened to match — and it surfaced only on the terminal, probed on the runtime's
   port and refused for "publishing no host port".
4. **A container registered for cleanup inside `$(…)` is registered in a subshell**, so the failure
   path had nothing to clean and left orphans behind every aborted run.

Proof, after the fixes: two swaps (`t1→t2→t3`) and one rollback, with four request loops at 200 ms —
`app` **243/243**, `agent-api` host **243/243**, `runtime` host **243/243**, and the in-network
`agent-api` alias **244/244**. Zero failed requests. Both refusals fire on demand: a default-variant
image under a `-minutes` tag (F67) and a terminal declaring `agent_api=2` against a server answering
`api=1` (F55/F77), neither of which moved anything.

## Adoption — the one step that still costs a window

`app.dev.vexa.ai` is already an nginx hop, so adopting it is a config edit (see `../nginx/`). The
**server** stable ports are not: `:18500` and `:18490` are published by the containers themselves,
and a port cannot be handed over without the holder letting go. Taking the `ports:` mapping out of
compose and recreating agent-api and runtime once is the last "out / in" this stack needs.

Until that is done `deploy.sh` still switches the alias correctly, and everything reaching a server
over a host port — the flows lane above all — keeps talking to the previous container. That split
is invisible (both healthy, both answering, different builds), so it is worth doing properly rather
than half.

## Never touched

`vexa-worker-*`. Workers are per-dispatch: a turn in flight finishes inside the container it started
in, and the new worker image applies to the next spawn. Every run snapshots the running worker set
and reports any difference rather than promising there is none.
