# Moving the Minutes stack off the control rig (Vexa-ai/vexa#1783)

For whoever applies deploys to the `vexa-minutes` stack (app.dev). The control rig
(`deploy/dogfood/rig/`) and the `agent_mcp.py` composition root that loaded it are removed from the
repository on this branch; every tool the stack's workers still need is served by the gateway's one
`/mcp`. The running stack keeps its own copies until these steps are applied.

## What the stack runs today (read 2026-10-12)

| piece | what it is |
|---|---|
| systemd user unit `vexa-minutes-agent-mcp` | `agent_mcp.py` from `~/minutes-connections-20261007`, loading a copy of the rig plus import, Connections, clock, chat-name and CRM tools; listens on `172.18.0.1:18321` |
| gateway env `AGENT_MCP_URL=http://172.18.0.1:18321` | set by `minutes.pinned.compose.json`; the stack's current gateway image sends workers' `/mcp` there |
| systemd user unit `vexa-hot-share-mcp-0909` | a second rig, from `~/dev/vexa-minutes-tool-restore-0915`, on `127.0.0.1:18311`; `crm.compose.json` points agent-api's `VEXA_MCP_URL` at it, overridden later by `minutes.pinned.compose.json` |
| nginx `rig.dev.vexa.ai` | `sites-enabled/rig.dev.vexa.ai.conf`, proxying to `127.0.0.1:18310`, which nothing listens on. The same file also serves `mail.dev.vexa.ai` (mailpit), which stays |
| `hot-mc.sh`, block "the control MCP" | applies `adaptations-rig/*.patch` to the rig copy and restarts the unit |
| compose `mcp` (`vexa-minutes-mcp-1`) | the product MCP. `ADMIN_API_URL`, `MEETING_API_URL`, `FLOWS_API_URL` and `AGENT_API_URL` are already set |
| agent-api `VEXA_MCP_URL=http://gateway:8000/mcp` | already the gateway; nothing to change |

## What workers lose, by decision

| rig tool | outcome |
|---|---|
| `start_onboarding`, `confirm_login`, `auth_link`, `auth_claim` | removed. Sign-in happens at the edge (ADR-0041), never inside a conversation |
| `deeplink` | removed. Composing terminal URLs is the client's concern |
| `bot_schedule` | not ported. It returns as a flows schedule trigger (ADR-0046) |
| CRM tools (`crm_tools.py`) | out of the product and this repository. They are not served after the switch |
| rehearsal, seeding and maintainer verbs (`rehearse`, `meeting_seed`, `user_ensure`, `fact_emit`, `friction_dump`, …) | not product tools. Rehearsal runs from `deploy/dogfood/rehearse` |

Every other rig tool has a product tool. The renamed ones: `meeting_info` → `get_meeting`,
`meeting_participants` → `get_meeting_participants`, `meeting_delete` → `delete_meeting`,
`settings` (set) → `settings_set`, `workspace_regime` (set) → `workspace_regime_set`, and
`mark_scaffolded` → `validate` (the first claim a person stands behind marks the desk ready).

## The switch

All on bbb. Check `df -h /` before building; stop if free space is under ~100 GB.

1. **Build the five images from this branch** in a throwaway worktree: `core/gateway/services/gateway`,
   `core/gateway/services/mcp` (the MCP moved into the gateway module in 0.13.3),
   `core/identity/services/admin-api`, `core/agent/services/agent-api` and `core/agent/worker`
   (`vexaai/vexa-gateway`, `vexaai/vexa-mcp`, `vexaai/vexa-admin-api`, `vexaai/vexa-agent-api`,
   `vexaai/vexa-agent-worker` from 0.13.3; `v012-` before). Tag them under a new name, never over
   the stack's current tags.
2. **Write a new lock** for `compose.py` with those image IDs, and with `AGENT_MCP_URL` removed from
   the gateway's environment in `minutes.pinned.compose.json`. This branch's gateway has one `/mcp`
   upstream (`MCP_URL=http://mcp:8010`) and does not read `AGENT_MCP_URL`. Record the old lock and
   the current image IDs first: they are the rollback.
3. **Drain workers.** A running worker keeps the toolbelt it was started with. Wait until no worker
   container is running, or accept that those turns finish on the old tools.
4. **Apply:** `python compose.py <new lock> <group> gateway mcp admin-api agent-api --apply` (`<group>` as the lock names the stack, `minutes` in the README example), plus the
   worker image wherever the lock names it.
5. **Retire the rig processes:**
   `systemctl --user stop vexa-minutes-agent-mcp vexa-hot-share-mcp-0909`, then `disable` both.
   Keep the unit files for one clean day.
6. **Retire `rig.dev.vexa.ai`:** delete the `rig.dev.vexa.ai` server blocks (the `listen 443` block
   and the name in the port-80 redirect) from `sites-enabled/rig.dev.vexa.ai.conf`, keeping
   `mail.dev.vexa.ai`. Then run `nginx -t` and reload nginx. Remove the DNS record for `rig.dev.vexa.ai`.
7. **Drop the rig block from `hot-mc.sh`:** delete `RIG_DIR`, the `adaptations-rig/*.patch` loop and
   the unit restart.
8. **Check:**
   - `python stack.py <new lock>` exits 0.
   - A new worker's `tools/list` shows `open_page`, `workspace_target`, `company_context`,
     `get_meeting`, `settings` and `workspace_push`.
   - In one chat, as a person: open a page, retarget the chat to a workspace, read the transcript,
     and change one setting.
   - `ss -ltn` shows nothing on 18311 or 18321.

## Rollback

1. `python compose.py <old lock> <group> gateway mcp admin-api agent-api --apply`. This restores the old
   images and `AGENT_MCP_URL`.
2. `systemctl --user enable --now vexa-minutes-agent-mcp vexa-hot-share-mcp-0909`.
3. Restore `sites-enabled/rig.dev.vexa.ai.conf` from `/etc/nginx/.backups/`, run `nginx -t`, and
   reload nginx.
4. Restore the `hot-mc.sh` block from its git history.

The rig source a rollback runs is the copy already on the host (`~/minutes-connections-20261007`
and `~/dev/vexa-minutes-tool-restore-0915`). The rollback needs nothing from this repository.
