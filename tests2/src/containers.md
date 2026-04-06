---
needs: [GATEWAY_URL, API_TOKEN, DEPLOY_MODE]
gives: [LIFECYCLE_OK, ORPHAN_COUNT, ZOMBIE_COUNT]
---

use: lib/http
use: lib/docker

# Container Lifecycle

> **Why:** Stopped-but-not-removed containers leak memory, disk, and PIDs. In production with hundreds of meetings/day, this kills the host.
> **What:** Create containers, stop them, verify they're fully removed from `docker ps -a`. Check for pre-existing zombies.
> **How:** Baseline count, create meeting_bot + browser_session, stop, verify count returns to baseline, scan for zombie processes.

## state

    BASELINE     = 0
    ORPHAN_COUNT = 0
    ZOMBIE_COUNT = 0
    LIFECYCLE_OK = false

## steps

```
1. baseline
   if DEPLOY_MODE == "compose":
       do: docker ps -a --filter "name=meeting-" --format '{{.Names}}' | wc -l
   if DEPLOY_MODE == "lite":
       do: docker exec vexa ps aux | wc -l
   => BASELINE = count
   on_fail: continue

for TYPE in [
    {name: "meeting_bot",     data: '{"platform":"google_meet","native_meeting_id":"lifecycle-test","bot_name":"LC Test"}'},
    {name: "browser_session", data: '{"mode":"browser_session","bot_name":"LC Browser"}'}
]:
    2. create
       call: http.post_json(URL="{GATEWAY_URL}/bots", DATA='{TYPE.data}', TOKEN={API_TOKEN})
       => BOT_ID = BODY.id
       on_fail: continue

    3. wait
       do: sleep 10

    4. stop
       call: http.delete(URL="{GATEWAY_URL}/bots/{BOT_ID}", TOKEN={API_TOKEN})
       on_fail: continue

    5. wait_removal
       do: sleep 15

    6. verify_gone
       if DEPLOY_MODE == "compose":
           do: docker ps -a --filter "name=meeting-{BOT_ID}" --format '{{.Names}}' | wc -l
       if DEPLOY_MODE == "lite":
           do: docker exec vexa ps aux | grep -c "{BOT_ID}" || echo 0
       if count > 0:
           ORPHAN_COUNT += 1
           emit FAIL "{TYPE.name}: stopped but not removed"
       else:
           emit PASS "{TYPE.name}: properly removed"
       on_fail: continue

7. zombies
   if DEPLOY_MODE == "compose":
       do: docker ps -a --filter "status=exited" --filter "name=meeting-" --format '{{.Names}}' | wc -l
   if DEPLOY_MODE == "lite":
       do: docker exec vexa ps aux | grep -c '[Z]' || echo 0
   => ZOMBIE_COUNT = count
   if ZOMBIE_COUNT > 0: emit FINDING "{ZOMBIE_COUNT} zombies"
   else: emit PASS "no zombies"
   on_fail: continue

8. summary
   => LIFECYCLE_OK = (ORPHAN_COUNT == 0 and ZOMBIE_COUNT == 0)
   emit FINDING "orphans={ORPHAN_COUNT} zombies={ZOMBIE_COUNT}"
```
