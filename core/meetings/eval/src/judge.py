#!/usr/bin/env python3
# judge — the 3-metric benchmark for the synthetic meeting, scored against the
# ground truth the driver logged. Platform-agnostic (PLATFORM=teams|zoom|google_meet).
#   1. COMPLETENESS — was each truth turn transcribed at all (any label)?
#   2. LEAKAGE      — does a segment's CONTENT self-ID a speaker != its label?
#                     (definitive content check; clips say "Boris here…")
#   3. ATTRIBUTION  — of NAMED segments, label == true speaker (precision + unknown%).
# Reads ground truth from TRUTH_LOG and the live transcript from TRANSCRIPTS_BASE.
# (Eyeball the same meeting live in the extension; this is the automated half.)
import json, re, os, urllib.request, statistics as st
from pathlib import Path

NATIVE = os.environ.get("NATIVE_ID", "")
PLATFORM = os.environ.get("PLATFORM", "google_meet")
TRUTH = os.environ.get("TRUTH_LOG", str(Path(__file__).parent.parent / "truth.jsonl"))
TBASE = os.environ.get("TRANSCRIPTS_BASE", "http://localhost:8056").rstrip("/")

turns = [json.loads(l) for l in open(TRUTH) if l.strip()]
# TRANSCRIPT_FILE → score a `{segments:[…]}` JSON dumped from another source (the STANDALONE bot's
# transcript.v1 redis stream, via read-redis-transcript.mjs) instead of the gateway HTTP. Symmetric
# with analyze.mjs's TRANSCRIPT_FILE branch — same scorer either way. Used by the bot-local harness,
# where the carved bot publishes to redis with no gateway meeting-record to fetch.
TF = os.environ.get("TRANSCRIPT_FILE")
d = json.load(open(TF)) if TF else json.load(urllib.request.urlopen(f"{TBASE}/transcripts/{PLATFORM}/{NATIVE}"))
segs = [s for s in (d.get('segments') or []) if (s.get('text') or '').strip()]

FIX = {'Врра':'Вера','ИгорИ':'Игорь','ДмДтрий':'Дмитрий','ДДитрий':'Дмитрий','ДДитри':'Дмитрий',
       'ЖЖнна':'Жанна','Жжнна':'Жанна','ЗоЗ':'Зоя','ЗоЗ ':'Зоя','Зоя':'Зоя'}
EN2RU = {'anna':'Анна','boris':'Борис','vera':'Вера','galina':'Галина','egor':'Егор',
         'zhanna':'Жанна','zoya':'Зоя','igor':'Игорь','dmitry':'Дмитрий'}
def nrm(s):
    # Map a captured label to a canonical Russian speaker. Handles the bot-name
    # labels we launch with ("spk-Dmitry" → Дмитрий) and OCR-ish glitches (FIX).
    if not s: return s
    s = FIX.get(s, s)
    m = re.match(r'spk[-_ ](.+)', s, re.I)
    if m:
        en = m.group(1).strip().lower()
        return EN2RU.get(ALIAS.get(en, en), s)
    return s
ALIAS = {'zira':'vera','vela':'vera','galena':'galina','dimitri':'dmitry','dimitry':'dmitry',
         'yegor':'egor','zoia':'zoya','ana':'anna','jana':'zhanna','jeanne':'zhanna','jan':'zhanna',
         'soya':'zoya','toy':'zoya','aileen':'galina','etree':'dmitry','dtree':'dmitry'}
def lead(t):
    for w in re.findall(r"[a-z]+", (t or '').lower()[:34]):
        w = ALIAS.get(w, w)
        if w in EN2RU: return EN2RU[w]
    return None
UNK = ('Speaker','You')

# ---- align GT (startMs ms) to segs (start sec) via lead-name matches → offset L (sec) ----
maxT = max(t['startMs'] for t in turns)/1000.0
rsegs = [s for s in segs if s.get('start',0) > maxT - 260]            # this session only
offs=[]
for s in rsegs:
    nm = lead(s.get('text',''))
    if not nm: continue
    cand=[t for t in turns if t['ru']==nm]
    if cand:
        sm=s['start']; b=min(cand,key=lambda t:abs(sm-t['startMs']/1000))
        if abs(sm-b['startMs']/1000)<15: offs.append(sm-b['startMs']/1000)
offs.sort(); L = st.median(offs) if len(offs)>=3 else 2.0
def gtAt(sec):
    ms=(sec-L)*1000; cov=[t for t in turns if t['startMs']-1500<=ms<=t['endMs']+1500]
    return min(cov,key=lambda t:abs(ms-(t['startMs']+t['endMs'])/2))['ru'] if cov else None

rturns=[t for t in turns if t['startMs']/1000 > maxT-260]
print(f"=== BENCHMARK  (L={L:.1f}s · {len(rturns)} truth turns · {len(rsegs)} segments) ===")

# 1) COMPLETENESS — each truth turn covered by SOME segment (any label) in its window
cov=0
for t in rturns:
    a,b = t['startMs']/1000+L, t['endMs']/1000+L
    if any(a-2 <= s['start'] <= b+2 for s in rsegs): cov+=1
print(f"1. COMPLETENESS (turn transcribed at all): {cov}/{len(rturns)} = {100*cov/max(1,len(rturns)):.0f}%")

# 2) LEAKAGE — segment content self-IDs a speaker that differs from its label
leak=ided=0; ex=[]
for s in rsegs:
    nm = lead(s.get('text',''))
    if not nm: continue
    ided+=1; lab=nrm(s.get('speaker'))
    if lab not in UNK and lab!=nm: leak+=1; ex.append((lab,nm,(s.get('text') or '')[:40]))
print(f"2. LEAKAGE (content of A labeled B): {leak}/{ided} self-IDing segs = {100*leak/max(1,ided):.0f}%")
for lab,nm,tx in ex[:5]: print(f"     ✗ [{lab}] but content says {nm}: «{tx}»")

# 3) ATTRIBUTION — of NAMED segs, label == true speaker (by time, fallback to content)
c=w=u=0
for s in rsegs:
    lab=nrm(s.get('speaker'))
    true = lead(s.get('text','')) or gtAt(s['start'])
    if lab in UNK: u+=1
    elif true is None: continue
    elif lab==true: c+=1
    else: w+=1
named=c+w
print(f"3. ATTRIBUTION (named-rate {100*named/max(1,len(rsegs)):.0f}%): of named, correct={c} wrong={w} → precision {100*c/max(1,named):.0f}%  | unknown {u} ({100*u/max(1,len(rsegs)):.0f}%)")

# Grep-friendly machine line for verdict.mjs (the bot-local harness aggregates this with analyze's
# SCORE line against BASELINE.md). LEAKAGE is the definitive content-vs-label check → a HARD gate;
# attribution precision is reported but NOT hard-gated (it over-counts under /speak latency drift).
# Counts, not just percentages, so the gate reads them directly.
print(f"\nJUDGE completeness={cov}/{len(rturns)} completeness_pct={100*cov/max(1,len(rturns)):.0f} "
      f"leakage={leak} leakage_pct={100*leak/max(1,ided):.0f} attribution_pct={100*c/max(1,named):.0f} "
      f"wrong={w} unknown={u} named={named} truth_turns={len(rturns)}")
