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

# Build CDP URL — use wss:// for HTTPS gateways (Playwright's connectOverCDP
# fetches the URL then downgrades to ws://, which fails behind TLS terminators)
if [[ "$GATEWAY_URL" == https://* ]]; then
    CDP_URL="wss://${GATEWAY_URL#https://}/b/$SESSION_TOKEN/cdp"
else
    CDP_URL="$GATEWAY_URL/b/$SESSION_TOKEN/cdp"
fi

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

# ── CDP auto-admit (with retry loop) ─────────────
echo "  running CDP auto-admit..."

# Retry up to 6 times (30s total) — the admit UI can take a few seconds to appear
ADMIT_RESULT="FAIL:not_attempted"
for attempt in $(seq 1 6); do
    if [ "$PLATFORM" = "google_meet" ]; then
        ADMIT_RESULT=$(node -e "
const {chromium}=require('playwright');
(async()=>{
    const b=await chromium.connectOverCDP('$CDP_URL',{timeout:15000});
    const ctx=b.contexts()[0];
    const p=ctx.pages().find(p=>p.url().includes('meet.google.com'));
    if(!p){console.log('FAIL:no_meeting_page');await b.close();return}

    // All known admit button patterns
    const admitSelectors=[
        'button[aria-label^=\"Admit \"]',
        'button[aria-label*=\"Admit\"]',
        'button:has-text(\"Admit\")',
        'button:has-text(\"Let in\")',
        'button:has-text(\"Accept\")',
    ];

    async function findAndClick(selectors){
        for(const sel of selectors){
            const el=p.locator(sel).first();
            if(await el.isVisible({timeout:500}).catch(()=>false)){
                await el.click();
                return sel;
            }
        }
        return null;
    }

    // Phase 1: admit button already visible
    let hit=await findAndClick(admitSelectors);
    if(hit){console.log('ADMITTED:phase1:'+hit);await b.close();return}

    // Phase 2: open people/participants panel to reveal waiting guests
    const panelSelectors=[
        'button[aria-label*=\"Show everyone\"]',
        'button[aria-label*=\"People\"]',
        'button[aria-label*=\"people\"]',
        'button[aria-label*=\"Participants\"]',
        'button[aria-label*=\"participants\"]',
        'button[data-tooltip*=\"People\"]',
        'button[data-tooltip*=\"people\"]',
    ];
    for(const sel of panelSelectors){
        const el=p.locator(sel).first();
        if(await el.isVisible({timeout:300}).catch(()=>false)){await el.click();break}
    }
    await p.waitForTimeout(1500);
    hit=await findAndClick(admitSelectors);
    if(hit){console.log('ADMITTED:phase2:'+hit);await b.close();return}

    // Phase 3: expand the 'waiting to join' / 'N guests' section
    const expandSelectors=[
        'text=/Waiting to join/i',
        'text=/waiting to join/i',
        'text=/Admit \\\\d+ guest/i',
        'text=/\\\\d+ waiting/i',
    ];
    for(const sel of expandSelectors){
        const el=p.locator(sel).first();
        if(await el.isVisible({timeout:300}).catch(()=>false)){await el.click();break}
    }
    await p.waitForTimeout(1500);
    hit=await findAndClick(admitSelectors);
    if(hit){console.log('ADMITTED:phase3:'+hit);await b.close();return}

    // Phase 4: try 'Admit all' as last resort
    const admitAll=p.locator('button:has-text(\"Admit all\")').first();
    if(await admitAll.isVisible({timeout:500}).catch(()=>false)){
        await admitAll.click();
        console.log('ADMITTED:phase4:admit_all');
        await b.close();return;
    }

    console.log('RETRY:no_admit_button');
    await b.close();
})().catch(e=>{console.error('ERROR:'+e.message);process.exit(1)});
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
        await p.waitForSelector('[data-tid=\"lobby-admit-all\"],button[aria-label*=\"Admit\"]',{timeout:10000}).catch(()=>{});
    if(await btn.isVisible().catch(()=>false)){await btn.click();console.log('ADMITTED')}
    else console.log('RETRY:no_admit_button');
    await b.close();
})().catch(e=>{console.error('ERROR:'+e.message);process.exit(1)});
" 2>&1)
    fi

    if echo "$ADMIT_RESULT" | grep -q "ADMITTED"; then
        pass "CDP admit ($attempt/6): $ADMIT_RESULT"
        break
    fi
    info "attempt $attempt/6: $ADMIT_RESULT"
    sleep 5
done

if ! echo "$ADMIT_RESULT" | grep -q "ADMITTED"; then
    fail "CDP admit failed after 6 attempts: $ADMIT_RESULT"
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
