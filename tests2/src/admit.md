---
needs: [GATEWAY_URL, API_TOKEN, SESSION_TOKEN, MEETING_PLATFORM, NATIVE_MEETING_ID]
gives: [BOT_ADMITTED]
---

use: lib/http

# Admit Bot

> **Why:** Bots wait in the meeting lobby. Someone must click Admit. Automating this removes a manual bottleneck.
> **What:** Use Playwright on the host's browser session to click the platform-specific Admit button.
> **How:** Connect to host browser via CDP proxy, find the Admit button (GMeet/Teams selectors), click it. Fall back to human if selectors fail.

## state

    BOT_ADMITTED = false

## steps

```
1. click_admit
   if MEETING_PLATFORM == "google_meet":
       do: |
           node -e "
           const {chromium}=require('playwright');
           (async()=>{
               const b=await chromium.connectOverCDP('{GATEWAY_URL}/b/{SESSION_TOKEN}/cdp');
               const p=b.contexts()[0].pages().find(p=>p.url().includes('meet.google.com'));
               if(!p){console.error('No meeting tab');process.exit(1)}
               const btn=await p.waitForSelector('[aria-label*=\"people\"]',{timeout:10000}).catch(()=>null);
               if(btn)await btn.click();
               await p.waitForTimeout(2000);
               const admit=await p.waitForSelector('text=/Admit/i',{timeout:30000});
               await admit.click();
               await p.waitForTimeout(1000);
               const confirm=await p.\$('text=/Admit all/i');
               if(confirm)await confirm.click();
               console.log('ADMITTED');
               await b.close();
           })().catch(e=>{console.error(e.message);process.exit(1)});
           "
       expect: stdout contains ADMITTED

   if MEETING_PLATFORM == "teams":
       do: |
           node -e "
           const {chromium}=require('playwright');
           (async()=>{
               const b=await chromium.connectOverCDP('{GATEWAY_URL}/b/{SESSION_TOKEN}/cdp');
               const p=b.contexts()[0].pages().find(p=>p.url().includes('teams'));
               const btn=await p.waitForSelector('[data-tid=\"lobby-admit-all\"],text=/Admit/i',{timeout:30000});
               await btn.click();
               console.log('ADMITTED');
               await b.close();
           })().catch(e=>{console.error(e.message);process.exit(1)});
           "
       expect: stdout contains ADMITTED

   emit PASS "admit clicked"
   on_fail: ask

   if fails:
       1a. manual [human]
           ask: "Auto-admit failed. Admit the bot manually, then type 'done'."

2. verify
   call: http.poll_until(URL="{GATEWAY_URL}/bots/status", TOKEN={API_TOKEN}, FIELD="status", VALUE="active", MAX=6, INTERVAL=5)
   => BOT_ADMITTED = true
   emit PASS "bot admitted and active"
   on_fail: stop
```
