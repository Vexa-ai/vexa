#!/usr/bin/env python3
"""TIME-ALIGNED 3-track diarization viewer: Deepgram (ref) | ours baseline |
ours overlap-stitch. Every turn is positioned by its real timestamp (top =
start*SCALE), so a horizontal line crosses all three columns at the same
instant — switch placement lines up visually. Click any turn to play.
All three are REAL transcripts (Deepgram API / local Whisper).
"""
import json, os, html

HERE = os.path.dirname(__file__); S = os.path.join(HERE, "samples")
AUDIO = "dgclip.wav"
DG = os.path.join(HERE, "deepgram", "deepgram_clip5min.json")
SCALE = 30  # px per second

dgw = json.load(open(DG))["results"]["channels"][0]["alternatives"][0]["words"]
dur = max(w["end"] for w in dgw)

def dg_turns():
    out = []
    for w in dgw:
        sp = f"spk{w['speaker']}"; t = w.get("punctuated_word", w["word"])
        if out and out[-1]["spk"] == sp: out[-1]["w"].append(t); out[-1]["end"] = w["end"]
        else: out.append(dict(spk=sp, start=w["start"], end=w["end"], w=[t]))
    return [dict(spk=o["spk"], start=o["start"], end=o["end"], text=" ".join(o["w"]), stitched=False) for o in out]

def smooth_islands(turns, max_island_s=1.5):
    """ONLINE-FAITHFUL island filter: single forward pass with a ONE-SEGMENT
    bounded look-ahead — exactly what the live pipeline can do inside its
    existing LocalAgreement/submitInterval confirmation buffer. When a new
    segment arrives, look back at the previous two (P, island I): if I is short
    and P.spk == newseg.spk != I.spk, relabel I into P. No iterate-to-fixpoint,
    no arbitrary future access. Then merge adjacent same-speaker turns."""
    turns = sorted([dict(t) for t in turns], key=lambda x: x["start"])
    res = []
    def absorb(dst, src):
        dst["end"] = src["end"]
        dst["text"] = (dst["text"] + " " + src["text"]).strip()
        dst["stitched"] = dst.get("stitched") or src.get("stitched")
        if src.get("conf") is not None: dst["conf"] = src.get("conf")
        if src.get("loser"): dst["loser"] = src.get("loser")
    for t in turns:
        # one-step look-ahead: t confirms whether res[-1] was an island.
        # NEVER absorb a stitched (overlap-welded) segment — those are
        # intentional and must stay visible; only collapse clustering-error
        # islands (non-stitched short segments).
        if len(res) >= 2:
            P, I = res[-2], res[-1]
            if (not I.get("stitched") and I["end"] - I["start"] <= max_island_s
                    and P["spk"] == t["spk"] and I["spk"] != P["spk"]):
                absorb(P, I); res.pop()
        # merge adjacent same-speaker, but keep stitched segments pinned as
        # their own visible span (don't merge a stitched seg, or merge into one)
        if res and res[-1]["spk"] == t["spk"] and not t.get("stitched") and not res[-1].get("stitched"):
            absorb(res[-1], t)
        else:
            res.append(dict(t))
    return res

def load(path, smooth=True):
    turns = [dict(spk=s["speaker"], start=s["start"], end=s["end"], text=s["text"],
                  stitched=s.get("stitched", False),
                  conf=s.get("conf"), loser=s.get("loser")) for s in json.load(open(path))["transcript"]]
    return smooth_islands(turns) if smooth else turns

cols = [
    ("Deepgram reference", dg_turns()),
    ("Ours BASELINE (Whisper)", load(os.path.join(S, "dgclip_ship.baseline.json"))),
    ("Ours STITCH + confidence-pick (Whisper)", load(os.path.join(S, "dgclip_ship.stitch.json"))),
]

PALETTE = ["#2563eb","#dc2626","#059669","#d97706","#7c3aed","#0891b2","#be185d","#65a30d"]
def cmap(turns):
    u = []
    for t in turns:
        if t["spk"] not in u: u.append(t["spk"])
    return {s: PALETTE[i % len(PALETTE)] for i, s in enumerate(sorted(u))}

def render_col(turns):
    cm = cmap(turns); turns = sorted(turns, key=lambda x: x["start"]); out = []
    for i, t in enumerate(turns):
        top = t["start"] * SCALE
        # size by spoken duration (gaps show as empty timeline, not giant boxes),
        # but never overlap the next turn's start
        nxt = turns[i+1]["start"] * SCALE if i+1 < len(turns) else (dur * SCALE)
        h = max(20, min((t["end"] - t["start"]) * SCALE, nxt - top - 2))
        mm, ss = int(t["start"]//60), int(t["start"]%60)
        badge = ''
        if t.get("stitched"):
            tip = ''
            if t.get("loser"):
                lo = t["loser"]; tip = f" — dropped {html.escape(lo['speaker'])} (conf {lo['conf']}): “{html.escape(lo['text'])}”"
            badge = f'<span class=st title="overlap welded; kept higher Whisper confidence{tip}">⨝{(" "+str(t["conf"])) if t.get("conf") is not None else ""}</span>'
        full = html.escape(t["text"])
        out.append(
            f'<div class="turn{" stitched" if t.get("stitched") else ""}" style="top:{top:.0f}px;height:{h:.0f}px" '
            f'data-t="{t["start"]:.2f}" title="{full}">'
            f'<span class=spk style="color:{cm[t["spk"]]}">{html.escape(t["spk"])}</span>'
            f'<span class=ts>{mm:02d}:{ss:02d}</span>{badge}'
            f'<div class=tx>{full}</div></div>')
    return "\n".join(out), len(cm)

gridlines = "\n".join(
    f'<div class=grid style="top:{t*SCALE}px"><span class=gl>{t//60:02d}:{t%60:02d}</span></div>'
    for t in range(0, int(dur)+1, 30))

col_html = []
for title, turns in cols:
    body, n = render_col(turns)
    col_html.append((title, n, body))

H = int(dur * SCALE) + 40
doc = f"""<!doctype html><meta charset=utf-8>
<title>Diarization — time-aligned (Deepgram vs ours)</title>
<style>
 body{{font:12px/1.4 system-ui;margin:0;background:#fafafa}}
 header{{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:8px 14px;z-index:20}}
 header audio{{width:100%;height:32px}} h1{{font-size:14px;margin:0 0 6px}}
 .meta{{color:#888;font-weight:400;font-size:12px}}
 .heads{{position:sticky;top:54px;display:grid;grid-template-columns:60px 1fr 1fr 1fr;background:#fff;border-bottom:1px solid #ddd;z-index:19}}
 .heads div{{padding:6px 8px;font-size:12px;font-weight:700;color:#333;border-left:1px solid #eee}}
 .tl{{position:relative;height:{H}px;margin-left:0}}
 .grid{{position:absolute;left:0;right:0;border-top:1px dashed #e2e2e2}}
 .gl{{position:absolute;left:6px;top:-7px;font-size:10px;color:#bbb;background:#fafafa;padding:0 2px;font-variant-numeric:tabular-nums}}
 .col{{position:absolute;top:0;bottom:0;width:calc((100% - 60px)/3)}}
 .c0{{left:60px}} .c1{{left:calc(60px + (100% - 60px)/3)}} .c2{{left:calc(60px + 2*(100% - 60px)/3)}}
 .turn{{position:absolute;left:3px;right:3px;overflow:hidden;border-radius:5px;padding:2px 6px;cursor:pointer;background:#fff;box-shadow:0 0 0 1px #eee}}
 .turn:hover{{overflow:visible;z-index:10;box-shadow:0 2px 8px rgba(0,0,0,.18);background:#fff;height:auto!important;min-height:20px}}
 .turn.stitched{{background:#fffbeb;box-shadow:inset 3px 0 0 #d97706,0 0 0 1px #f3e2bf}}
 .spk{{font-weight:700;font-size:11px;margin-right:5px}}
 .ts{{color:#bbb;font-variant-numeric:tabular-nums;margin-right:5px;font-size:10px}}
 .st{{color:#b45309;font-size:10px;font-weight:700;margin-right:4px;cursor:help}}
 .tx{{font-size:12px;color:#222}}
</style>
<header>
 <h1>Diarization — time-aligned <span class=meta>· same height = same instant · click a turn to play · ⨝ = overlap welded (kept higher Whisper confidence) · {AUDIO}</span></h1>
 <audio id=a controls preload=metadata src="{AUDIO}"></audio>
</header>
<div class=heads><div>time</div>{''.join(f'<div>{html.escape(t)} — {n} spk</div>' for t,n,_ in col_html)}</div>
<div class=tl>
 {gridlines}
 <div class="col c0">{col_html[0][2]}</div>
 <div class="col c1">{col_html[1][2]}</div>
 <div class="col c2">{col_html[2][2]}</div>
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
print(f"wrote compare.html (time-aligned, {SCALE}px/s, {dur:.0f}s) | " +
      " | ".join(f"{t}={n}spk" for t,n,_ in col_html))
