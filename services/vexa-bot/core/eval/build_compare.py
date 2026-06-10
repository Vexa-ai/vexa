#!/usr/bin/env python3
"""Side-by-side diarization comparison viewer.
Left  = Deepgram speakers (reference). Right = our diarizer's clusters.
Same transcribed words on both sides (Deepgram's word timings reused);
each side colors/groups them by its own speaker labels. Click any turn to
seek the shared audio. Renders LOCAL generated eval data only.
"""
import json, os, sys, html
sys.path.insert(0, os.path.dirname(__file__))
from fast_sweep import replay

HERE = os.path.dirname(__file__)
S = os.path.join(HERE, "samples")
DG = os.path.join(HERE, "deepgram", "deepgram_clip5min.json")
AUDIO = "dgclip.wav"  # served from samples/

# our best config so far (swept vs Deepgram GT: frameAcc 0.784, 4 clusters)
CFG = dict(newSpeakerThreshold=0.70, veryFarThreshold=0.95,
           minSeedUtteranceMs=1500, newClusterCooldownMs=2000)

words = json.load(open(DG))["results"]["channels"][0]["alternatives"][0]["words"]
emb = json.load(open(os.path.join(S, "dgclip.capture.emb.json")))
commits = replay(emb["records"], CFG)  # [(speaker, startS, endS)]

def our_label(t):
    for sp, a, b in commits:
        if a <= t < b:
            return sp
    # nearest commit fallback
    best, bd = None, 1e9
    for sp, a, b in commits:
        d = min(abs(a - t), abs(b - t))
        if d < bd:
            bd, best = d, sp
    return best or "speaker_?"

# annotate each word with dg + our labels
rows = []
for w in words:
    rows.append(dict(
        t=w["start"], end=w["end"],
        txt=w.get("punctuated_word", w["word"]),
        dg=f"spk{w['speaker']}",
        ours=our_label(w["start"]),
    ))

def turns(rows, key):
    out, cur = [], None
    for r in rows:
        if cur is None or r[key] != cur["spk"]:
            cur = dict(spk=r[key], start=r["t"], words=[])
            out.append(cur)
        cur["words"].append(r["txt"])
    return out

# stable color per speaker label
PALETTE = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0891b2",
           "#be185d", "#65a30d", "#ea580c", "#4f46e5"]
def color_map(labels):
    uniq = []
    for l in labels:
        if l not in uniq:
            uniq.append(l)
    return {l: PALETTE[i % len(PALETTE)] for i, l in enumerate(sorted(uniq))}

dg_turns = turns(rows, "dg")
our_turns = turns(rows, "ours")
dg_colors = color_map([r["dg"] for r in rows])
our_colors = color_map([r["ours"] for r in rows])

def render(tns, colors):
    out = []
    for tn in tns:
        c = colors[tn["spk"]]
        mm, ss = int(tn["start"] // 60), int(tn["start"] % 60)
        out.append(
            f'<div class=turn data-t="{tn["start"]:.2f}">'
            f'<span class=spk style="color:{c}">{html.escape(tn["spk"])}</span>'
            f'<span class=ts>{mm:02d}:{ss:02d}</span>'
            f'<span class=txt>{html.escape(" ".join(tn["words"]))}</span></div>'
        )
    return "\n".join(out)

doc = f"""<!doctype html><meta charset=utf-8>
<title>Diarization compare — Deepgram vs ours</title>
<style>
 body{{font:14px/1.55 system-ui;margin:0;background:#fafafa}}
 header{{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:10px 16px;z-index:5}}
 header audio{{width:100%;height:34px}}
 h1{{font-size:15px;margin:0 0 6px}}
 .cols{{display:grid;grid-template-columns:1fr 1fr;gap:0}}
 .col{{padding:8px 14px;border-right:1px solid #eee}}
 .col h2{{position:sticky;top:64px;background:#fafafa;font-size:13px;color:#444;margin:0 0 8px;padding:6px 0;border-bottom:1px solid #eee}}
 .turn{{padding:4px 8px;margin:2px 0;border-radius:6px;cursor:pointer}}
 .turn:hover{{background:#eef2ff}}
 .spk{{font-weight:700;margin-right:8px;font-size:12px}}
 .ts{{color:#aaa;font-variant-numeric:tabular-nums;margin-right:8px;font-size:12px}}
 .txt{{}}
 .meta{{color:#888;font-size:12px;font-weight:400}}
</style>
<header>
 <h1>Diarization comparison <span class=meta>— click any turn to play from there · audio: {AUDIO}</span></h1>
 <audio id=a controls preload=metadata src="{AUDIO}"></audio>
</header>
<div class=cols>
 <div class=col><h2>Deepgram (reference) — {len(dg_colors)} speakers, {len(dg_turns)} turns</h2>{render(dg_turns, dg_colors)}</div>
 <div class=col><h2>Ours (nst {CFG['newSpeakerThreshold']}/vft {CFG['veryFarThreshold']}) — {len(our_colors)} clusters, {len(our_turns)} turns</h2>{render(our_turns, our_colors)}</div>
</div>
<script>
const a=document.getElementById('a');
document.querySelectorAll('.turn').forEach(el=>el.onclick=()=>{{
  const t=parseFloat(el.dataset.t);
  if(a.readyState>=1){{a.currentTime=t;a.play();}}
  else{{a.addEventListener('loadedmetadata',()=>{{a.currentTime=t;a.play();}},{{once:true}});a.load();}}
}});
</script>"""
open(os.path.join(S, "compare.html"), "w").write(doc)
print(f"wrote compare.html | Deepgram {len(dg_colors)} spk/{len(dg_turns)} turns | ours {len(our_colors)} clusters/{len(our_turns)} turns")
