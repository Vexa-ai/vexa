# meet-join — agent harness notes

The first brick (MANIFEST §1a). Develop it through its **hot debug container**, watched and driven by an agent. No infra, no full stack — one container + a browser.

## The watch loop (reproducible env, live source)

```
make debug URL="https://meet.google.com/xxx-xxxx-xxx"   # local egress (residential)
make debug-cloud URL="..." CLOUD_HOST=<host>            # other egress IP (bbb=2nd household; linode=datacenter)
```

- Image bakes only the environment (Xvfb + humanized X11 + noVNC + deps); `src/` and `scripts/` are mounted live and run via **tsx** → edit on host, re-run, instant, **no rebuild**.
- Container-only by design: the harness environment is reproducible or it is not evidence. `debug-join.ts` refuses to run on a bare host.
- Lens (the bot's own browser): **http://localhost:6080/vnc.html**
- Agent control of the bot's browser: `playwright connectOverCDP("http://localhost:9222")`
- Screenshots land in `debug-screenshots/` (mounted, readable from host).

## The agent is BOTH host and observer (Claude-in-Chrome MCP)

The live-platform test is fully agent-driven — no human needed to create or admit:

- **Create the meeting:** `mcp__Claude_in_Chrome__navigate` to `https://meet.google.com/new` (or `/landing`) in the user's logged-in Chrome, read the meeting code.
- **Observe the host side:** keep the host tab open; `read_page` / `find` / screenshot to see whether the bot actually appears in the participants list, whether an "Admit" prompt shows, whether it's really in the call.
- **Admit the bot:** click the Admit affordance from the host tab.
- **Cross-check against the brick's own state:** the bot logs `>>> [JOIN-STATE] …`. The oracle is only honest if the brick's state matches what the host tab actually shows.

> **This cross-check is the real oracle.** The brick reporting `admitted=true` on a DOM selector (`[data-participant-id]`) is NOT proof — verify against the host's participant list via the browser MCP. A mismatch (brick says admitted, host sees no bot) is a **false-positive admission** bug — exactly what this harness exists to catch (#444).

## Two variables under test (#444 / #407)

| Variable | Values | Finding so far |
|---|---|---|
| Input stack | synthetic (bare host) vs **humanized X11** (container) | synthetic → hard black-page block; humanized → reCAPTCHA that can resolve to lobby |
| Egress IP | residential vs datacenter | datacenter arm via `debug-cloud CLOUD_HOST=<linode>` — pending |

Gates (laptop, no infra): `make check` (isolation) · `npm run build` (standalone) · `npx tsc --noEmit`.
