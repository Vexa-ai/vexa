---
needs: [GATEWAY_URL, API_TOKEN, SESSION_TOKEN, MEETING_PLATFORM, NATIVE_MEETING_ID]
gives: [BOT_ADMITTED]
---

use: lib/http

# Admit Bot

> **Why:** Bots wait in the meeting lobby. Someone must click Admit. Automating this removes a manual bottleneck.
> **What:** Multi-phase CDP automation: find meeting page → open people panel → expand waiting section → click admit → verify active.
> **How:** Each phase checks if the admit button is already visible before acting. Phases are idempotent — re-running doesn't toggle state backwards. Falls back to human only after all phases fail.

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
               if(!p){console.log('FAIL:no_meeting_page');await b.close();return}

               // Helper: find visible admit button (specific first, then Admit all)
               async function findAdmit(){
                   const s=p.locator('button[aria-label^=\"Admit \"]').first();
                   if(await s.isVisible().catch(()=>false)) return s;
                   const a=p.locator('button:has-text(\"Admit all\")').first();
                   if(await a.isVisible().catch(()=>false)) return a;
                   return null;
               }

               // Phase 1: already visible (panel open, section expanded from prior run)
               let btn=await findAdmit();
               if(btn){
                   const l=await btn.getAttribute('aria-label').catch(()=>'Admit all');
                   await btn.click();
                   console.log('ADMITTED:phase1:'+l);
                   await b.close();return;
               }

               // Phase 2: open people panel
               for(const sel of ['button[aria-label*=\"Show everyone\"]','button[aria-label*=\"People\"]','button[aria-label*=\"people\"]']){
                   const el=p.locator(sel).first();
                   if(await el.isVisible().catch(()=>false)){await el.click();break}
               }
               await p.waitForTimeout(2000);
               btn=await findAdmit();
               if(btn){
                   const l=await btn.getAttribute('aria-label').catch(()=>'Admit all');
                   await btn.click();
                   console.log('ADMITTED:phase2:'+l);
                   await b.close();return;
               }

               // Phase 3: expand collapsed 'Waiting to join' section
               // Google Meet collapses the waiting section into 'Admit N guest'
               const expandSelectors=['text=/Admit \\\\d+ guest/i','text=/Waiting to join/i','text=/Waiting to be admitted/i'];
               for(const sel of expandSelectors){
                   const el=p.locator(sel).first();
                   if(await el.isVisible().catch(()=>false)){await el.click();break}
               }
               await p.waitForTimeout(2000);
               btn=await findAdmit();
               if(btn){
                   const l=await btn.getAttribute('aria-label').catch(()=>'Admit all');
                   await btn.click();
                   console.log('ADMITTED:phase3:'+l);
                   await b.close();return;
               }

               // Phase 4: retry expand (may have toggled wrong direction)
               for(const sel of expandSelectors){
                   const el=p.locator(sel).first();
                   if(await el.isVisible().catch(()=>false)){await el.click();break}
               }
               await p.waitForTimeout(2000);
               btn=await findAdmit();
               if(btn){
                   const l=await btn.getAttribute('aria-label').catch(()=>'Admit all');
                   await btn.click();
                   console.log('ADMITTED:phase4:'+l);
                   await b.close();return;
               }

               console.log('FAIL:all_phases_exhausted');
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
               if(!p){console.log('FAIL:no_teams_page');await b.close();return}

               async function findAdmit(){
                   const all=p.locator('[data-tid=\"lobby-admit-all\"]').first();
                   if(await all.isVisible().catch(()=>false)) return all;
                   const btn=p.locator('button[aria-label*=\"Admit\"]').first();
                   if(await btn.isVisible().catch(()=>false)) return btn;
                   return null;
               }

               let btn=await findAdmit();
               if(!btn){
                   // Wait up to 30s for admit to appear
                   await p.waitForSelector('[data-tid=\"lobby-admit-all\"],button[aria-label*=\"Admit\"]',{timeout:30000}).catch(()=>{});
                   btn=await findAdmit();
               }
               if(btn){await btn.click();console.log('ADMITTED')}
               else console.log('FAIL:no_admit_button');
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
   > Poll until bot reaches active. Confirms the click worked.
   for i in [1..12]:
       do: sleep 5
       call: http.get_json(URL="{GATEWAY_URL}/bots/status", TOKEN={API_TOKEN})
       if any bot with native_meeting_id == {NATIVE_MEETING_ID} has meeting_status == "active":
           => BOT_ADMITTED = true
           emit PASS "bot admitted and active"
           stop
   if BOT_ADMITTED == false:
       emit FAIL "bot not active after admit — click may have hit wrong element"
   on_fail: stop
```

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| Script prints ADMITTED but bot stays awaiting_admission | `text=/Admit/i` matched text node, not clickable button | Use `button[aria-label^="Admit "]` — targets actual button element | Never use bare text matching for clicks; use element-type + aria-label |
| Admit button not visible despite bot in lobby | People panel closed | Phase 2 opens panel before looking for button | Google Meet panels must be explicitly opened |
| Panel open but admit button still not visible | "Waiting to join" section is collapsed into "Admit N guest" header | Phase 3 clicks the collapsed header to expand | Google Meet collapses waiting section after ~10s |
| Expand click collapses instead of expanding | Toggle was already in expanded state from previous admit | Each phase checks `findAdmit()` before and after — idempotent | Check visibility before toggling; don't assume initial state |
| Validated with 4 consecutive stop→relaunch→admit cycles | All 4 passed | Multi-phase approach is robust | 2026-04-07: R1-R4 all PASS |
