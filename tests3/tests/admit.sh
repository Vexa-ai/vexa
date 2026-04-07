#!/usr/bin/env bash
# Auto-admit bot via CDP (multi-phase: panel → expand → click)
# Covers DoDs: bot#8
# Reads: .state/gateway_url, .state/api_token, .state/session_token, .state/native_meeting_id, .state/meeting_platform
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
SESSION_TOKEN=$(state_read session_token)
NATIVE_ID=$(state_read native_meeting_id)
PLATFORM=$(state_read meeting_platform)
CDP_URL="$GATEWAY_URL/b/$SESSION_TOKEN/cdp"

echo ""
echo "  admit"
echo "  ──────────────────────────────────────────────"

# Check if bot is already active (no lobby)
STATUS=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    if b.get('native_meeting_id')=='$NATIVE_ID':
        print(b.get('meeting_status',b.get('status','')))
        break
else: print('unknown')
" 2>/dev/null)

if [ "$STATUS" = "active" ]; then
    pass "bot already active — no admit needed"
    echo "  ──────────────────────────────────────────────"
    echo ""
    exit 0
fi

if [ "$STATUS" != "awaiting_admission" ]; then
    info "bot status: $STATUS (waiting for awaiting_admission...)"
    for i in $(seq 1 12); do
        sleep 5
        STATUS=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    if b.get('native_meeting_id')=='$NATIVE_ID':
        print(b.get('meeting_status',b.get('status','')))
        break
else: print('unknown')
" 2>/dev/null)
        [ "$STATUS" = "awaiting_admission" ] || [ "$STATUS" = "active" ] && break
    done
fi

if [ "$STATUS" = "active" ]; then
    pass "bot became active without admit"
    echo "  ──────────────────────────────────────────────"
    echo ""
    exit 0
fi

# ── CDP auto-admit ────────────────────────────────
echo "  running CDP auto-admit..."

if [ "$PLATFORM" = "google_meet" ]; then
    ADMIT_RESULT=$(node -e "
const {chromium}=require('playwright');
(async()=>{
    const b=await chromium.connectOverCDP('$CDP_URL',{timeout:15000});
    const p=b.contexts()[0].pages().find(p=>p.url().includes('meet.google.com'));
    if(!p){console.log('FAIL:no_meeting_page');await b.close();return}
    async function findAdmit(){
        const s=p.locator('button[aria-label^=\"Admit \"]').first();
        if(await s.isVisible().catch(()=>false)) return s;
        const a=p.locator('button:has-text(\"Admit all\")').first();
        if(await a.isVisible().catch(()=>false)) return a;
        return null;
    }
    // Phase 1: already visible
    let btn=await findAdmit();
    if(btn){await btn.click();console.log('ADMITTED:phase1');await b.close();return}
    // Phase 2: open people panel
    for(const sel of ['button[aria-label*=\"Show everyone\"]','button[aria-label*=\"People\"]']){
        const el=p.locator(sel).first();
        if(await el.isVisible().catch(()=>false)){await el.click();break}
    }
    await p.waitForTimeout(2000);
    btn=await findAdmit();
    if(btn){await btn.click();console.log('ADMITTED:phase2');await b.close();return}
    // Phase 3: expand waiting section
    for(const sel of ['text=/Admit \\\\d+ guest/i','text=/Waiting to join/i']){
        const el=p.locator(sel).first();
        if(await el.isVisible().catch(()=>false)){await el.click();break}
    }
    await p.waitForTimeout(2000);
    btn=await findAdmit();
    if(btn){await btn.click();console.log('ADMITTED:phase3');await b.close();return}
    console.log('FAIL:all_phases');
    await b.close();
})().catch(e=>{console.error(e.message);process.exit(1)});
" 2>&1)
elif [ "$PLATFORM" = "teams" ]; then
    ADMIT_RESULT=$(node -e "
const {chromium}=require('playwright');
(async()=>{
    const b=await chromium.connectOverCDP('$CDP_URL',{timeout:15000});
    const p=b.contexts()[0].pages().find(p=>p.url().includes('teams'));
    if(!p){console.log('FAIL:no_teams_page');await b.close();return}
    const btn=p.locator('[data-tid=\"lobby-admit-all\"],button[aria-label*=\"Admit\"]').first();
    if(!await btn.isVisible().catch(()=>false))
        await p.waitForSelector('[data-tid=\"lobby-admit-all\"],button[aria-label*=\"Admit\"]',{timeout:30000}).catch(()=>{});
    if(await btn.isVisible().catch(()=>false)){await btn.click();console.log('ADMITTED')}
    else console.log('FAIL:no_admit_button');
    await b.close();
})().catch(e=>{console.error(e.message);process.exit(1)});
" 2>&1)
fi

if echo "$ADMIT_RESULT" | grep -q "ADMITTED"; then
    pass "CDP admit: $ADMIT_RESULT"
else
    fail "CDP admit failed: $ADMIT_RESULT"
    echo ""
    echo "  ┌─────────────────────────────────────────┐"
    echo "  │  Auto-admit failed.                      │"
    echo "  │  Admit the bot manually, then press Enter │"
    echo "  └─────────────────────────────────────────┘"
    read -r -p "  Press Enter after admitting... "
fi

# ── Verify bot is active ──────────────────────────
echo "  verifying active status..."
for i in $(seq 1 12); do
    sleep 5
    STATUS=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    if b.get('native_meeting_id')=='$NATIVE_ID':
        print(b.get('meeting_status',b.get('status','')))
        break
else: print('unknown')
" 2>/dev/null)
    [ "$STATUS" = "active" ] && break
done

if [ "$STATUS" = "active" ]; then
    pass "bot active after admit"
else
    fail "bot not active after admit (status=$STATUS)"
    exit 1
fi

echo "  ──────────────────────────────────────────────"
echo ""
