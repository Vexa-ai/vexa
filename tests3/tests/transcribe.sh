#!/usr/bin/env bash
# Send TTS utterances via a SEPARATE speaker bot, fetch transcript from recorder, score.
# Key insight: a bot can't hear itself. Speaker bot (user B) speaks, recorder bot (user A) captures.
# Reads: .state/gateway_url, .state/api_token, .state/admin_token, .state/admin_url,
#        .state/native_meeting_id, .state/meeting_platform, .state/session_token
# Writes: .state/segments, .state/quality
source "$(dirname "$0")/../lib/common.sh"

GATEWAY_URL=$(state_read gateway_url)
API_TOKEN=$(state_read api_token)
ADMIN_URL=$(state_read admin_url)
ADMIN_TOKEN=$(state_read admin_token)
NATIVE_ID=$(state_read native_meeting_id)
PLATFORM=$(state_read meeting_platform)

echo ""
echo "  transcribe"
echo "  ──────────────────────────────────────────────"

# ── 1. Create speaker user + token ────────────────
# The recorder bot (API_TOKEN) can't hear itself. We need a separate speaker.
info "creating speaker user..."

# Find or create speaker user (don't use -f, we need to see 404)
SPEAKER_RESP=$(curl -s "$ADMIN_URL/admin/users/email/speaker@vexa.ai" \
    -H "X-Admin-API-Key: $ADMIN_TOKEN" -w "\n%{http_code}" 2>/dev/null)
SPEAKER_HTTP=$(echo "$SPEAKER_RESP" | tail -1)
SPEAKER_BODY=$(echo "$SPEAKER_RESP" | head -n -1)

if [ "$SPEAKER_HTTP" = "200" ]; then
    SPEAKER_USER_ID=$(echo "$SPEAKER_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
else
    # Create speaker user
    SPEAKER_BODY=$(curl -s -X POST "$ADMIN_URL/admin/users" \
        -H "X-Admin-API-Key: $ADMIN_TOKEN" -H "Content-Type: application/json" \
        -d '{"email":"speaker@vexa.ai","name":"Speaker Bot"}' 2>/dev/null)
    SPEAKER_USER_ID=$(echo "$SPEAKER_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
fi

if [ -z "$SPEAKER_USER_ID" ]; then
    fail "could not find or create speaker user"
    info "$SPEAKER_BODY"
    exit 1
fi

# Create token for speaker
SPEAKER_TOKEN=$(curl -s -X POST "$ADMIN_URL/admin/users/$SPEAKER_USER_ID/tokens?scopes=bot,browser,tx&name=speaker" \
    -H "X-Admin-API-Key: $ADMIN_TOKEN" 2>/dev/null | \
    python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)

if [ -z "$SPEAKER_TOKEN" ]; then
    fail "could not create speaker token"
    exit 1
fi
pass "speaker user ready (id=$SPEAKER_USER_ID)"

# ── 2. Launch speaker bot ─────────────────────────
info "launching speaker bot into meeting..."
SPEAKER_RESP=$(curl -s -X POST "$GATEWAY_URL/bots" \
    -H "X-API-Key: $SPEAKER_TOKEN" -H "Content-Type: application/json" \
    -d "{\"platform\":\"$PLATFORM\",\"native_meeting_id\":\"$NATIVE_ID\",\"bot_name\":\"Speaker\",\"voice_agent_enabled\":true,\"automatic_leave\":{\"no_one_joined_timeout\":300000}}" \
    -w "\n%{http_code}")
SPEAKER_HTTP=$(echo "$SPEAKER_RESP" | tail -1)
SPEAKER_BODY=$(echo "$SPEAKER_RESP" | head -n -1)

if [ "$SPEAKER_HTTP" != "200" ] && [ "$SPEAKER_HTTP" != "201" ] && [ "$SPEAKER_HTTP" != "202" ]; then
    fail "speaker bot creation failed (HTTP $SPEAKER_HTTP)"
    info "$SPEAKER_BODY"
    info "may need to admit speaker bot manually — continuing without TTS"
else
    pass "speaker bot launched"
fi

# ── 3. Admit speaker bot ──────────────────────────
if state_exists session_token; then
    info "waiting for speaker bot in lobby..."
    sleep 15

    # Check if speaker needs admission
    SPEAKER_STATUS=$(curl -sf -H "X-API-Key: $SPEAKER_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    if b.get('native_meeting_id')=='$NATIVE_ID':
        print(b.get('meeting_status',b.get('status','')))
        break
else: print('unknown')
" 2>/dev/null)

    if [ "$SPEAKER_STATUS" = "awaiting_admission" ]; then
        info "admitting speaker bot..."
        SESSION_TOKEN=$(state_read session_token)
        CDP_URL="$GATEWAY_URL/b/$SESSION_TOKEN/cdp"

        ADMIT_OUT=$(node -e "
const {chromium}=require('playwright');
(async()=>{
    const b=await chromium.connectOverCDP('$CDP_URL',{timeout:15000});
    const p=b.contexts()[0].pages().find(p=>p.url().includes('meet.google.com'));
    if(!p){console.log('FAIL:no_page');await b.close();return}
    async function findAdmit(){
        const s=p.locator('button[aria-label^=\"Admit \"]').first();
        if(await s.isVisible().catch(()=>false)) return s;
        const a=p.locator('button:has-text(\"Admit all\")').first();
        if(await a.isVisible().catch(()=>false)) return a;
        return null;
    }
    // Try all phases
    for(const phase of [1,2,3]){
        if(phase==2){
            for(const sel of ['button[aria-label*=\"Show everyone\"]','button[aria-label*=\"People\"]']){
                const el=p.locator(sel).first();
                if(await el.isVisible().catch(()=>false)){await el.click();break}
            }
            await p.waitForTimeout(2000);
        }
        if(phase==3){
            for(const sel of ['text=/Admit \\\\d+ guest/i','text=/Waiting to join/i']){
                const el=p.locator(sel).first();
                if(await el.isVisible().catch(()=>false)){await el.click();break}
            }
            await p.waitForTimeout(2000);
        }
        const btn=await findAdmit();
        if(btn){await btn.click();console.log('ADMITTED:phase'+phase);await b.close();return}
    }
    console.log('FAIL:no_admit');
    await b.close();
})().catch(e=>{console.error(e.message);process.exit(1)});
" 2>&1)
        if echo "$ADMIT_OUT" | grep -q "ADMITTED"; then
            pass "speaker admitted ($ADMIT_OUT)"
        else
            info "speaker auto-admit failed: $ADMIT_OUT"
            info "admit the speaker bot manually if needed"
        fi
    elif [ "$SPEAKER_STATUS" = "active" ]; then
        pass "speaker already active"
    fi

    # Wait for speaker to be active
    for i in $(seq 1 12); do
        SPEAKER_STATUS=$(curl -sf -H "X-API-Key: $SPEAKER_TOKEN" "$GATEWAY_URL/bots/status" | python3 -c "
import sys,json
for b in json.load(sys.stdin).get('running_bots',[]):
    if b.get('native_meeting_id')=='$NATIVE_ID':
        print(b.get('meeting_status',b.get('status','')))
        break
else: print('unknown')
" 2>/dev/null)
        [ "$SPEAKER_STATUS" = "active" ] && break
        sleep 5
    done
fi

# ── 4. Send TTS via speaker bot ──────────────────
UTTERANCES=(
    "Good morning everyone. Let's review the quarterly numbers."
    "Revenue increased by fifteen percent compared to last quarter."
    "Customer satisfaction score is ninety two percent."
)

echo "  sending ${#UTTERANCES[@]} TTS utterances via speaker bot..."
SENT=0
for text in "${UTTERANCES[@]}"; do
    TTS_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
        "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_ID/speak" \
        -H "X-API-Key: $SPEAKER_TOKEN" -H "Content-Type: application/json" \
        -d "{\"text\":\"$text\",\"voice\":\"alloy\"}" 2>/dev/null || echo "000")
    if [ "$TTS_CODE" = "202" ] || [ "$TTS_CODE" = "200" ]; then
        SENT=$((SENT + 1))
        info "sent: ${text:0:50}..."
    else
        info "TTS failed (HTTP $TTS_CODE): ${text:0:40}..."
    fi
    sleep 10
done

if [ "$SENT" -gt 0 ]; then
    pass "$SENT/${#UTTERANCES[@]} utterances sent via speaker bot"
else
    fail "no TTS utterances sent"
    info "speaker bot may not be active or voice_agent not enabled"
fi

# ── 5. Wait for pipeline ─────────────────────────
echo "  waiting 30s for transcription pipeline..."
sleep 30

# ── 6. Fetch transcript (from recorder bot's perspective) ──
RESP=$(curl -sf -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/transcripts/$PLATFORM/$NATIVE_ID")
SEGMENTS=$(echo "$RESP" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    segs=d.get('segments',[]) if isinstance(d,dict) else d
    print(len(segs))
except: print(0)
" 2>/dev/null)

state_write segments "${SEGMENTS:-0}"

if [ "${SEGMENTS:-0}" -gt 0 ]; then
    pass "transcript: $SEGMENTS segments captured by recorder"
else
    fail "0 segments — recorder did not capture speaker audio"
    info "known issue: bot-to-bot audio may not work without a human in the meeting"
fi

# ── 7. Basic quality check ───────────────────────
if [ "${SEGMENTS:-0}" -gt 0 ]; then
    QUALITY=$(echo "$RESP" | python3 -c "
import sys,json
ground_truth = ['good morning everyone', 'revenue increased', 'customer satisfaction']
d=json.load(sys.stdin)
segs=d.get('segments',[]) if isinstance(d,dict) else d
texts=' '.join(s.get('text','') for s in segs).lower()
matched=sum(1 for gt in ground_truth if gt in texts)
print(f'{matched}/{len(ground_truth)}')
" 2>/dev/null)
    state_write quality "$QUALITY"
    pass "quality: $QUALITY ground truth phrases found"
fi

# ── 8. Stop speaker bot ──────────────────────────
info "stopping speaker bot..."
curl -sf -X DELETE "$GATEWAY_URL/bots/$PLATFORM/$NATIVE_ID" \
    -H "X-API-Key: $SPEAKER_TOKEN" > /dev/null 2>&1 || true

echo "  ──────────────────────────────────────────────"
echo ""
