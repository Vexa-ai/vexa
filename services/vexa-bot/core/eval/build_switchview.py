#!/usr/bin/env python3
"""Transcript + speaker-change marks + audio. NO diarization, NO speaker labels.
Just: our Whisper words, and a divider wherever the segmentation model says the
speaker (probably) changed. Click any word or mark to play from there; the
current word highlights as it plays. Validate by ear: does each mark land where
the voice actually switches?
"""
import json, os, html
HERE = os.path.dirname(__file__); S = os.path.join(HERE, "samples")
AUDIO = "dgclip.wav"

words = json.load(open(os.path.join(S, "dgclip.words.json")))["words"]
bnds = json.load(open(os.path.join(S, "dgclip_bnd.boundaries.json")))["boundaries"]

# Consolidate raw boundaries into ONE switch mark per change-region (bursts
# within 0.75s collapse). A mark needs a "new voice" event (someone starts /
# direct change / overlap begins); lone "stops" are not switches.
NEWVOICE = {"silence→speaker", "speaker→speaker", "overlap-onset"}
def consolidate(bs, gap=0.75):
    bs = sorted(bs, key=lambda x: x["tMs"]); marks = []; i = 0
    while i < len(bs):
        cl = [bs[i]]; j = i + 1
        while j < len(bs) and (bs[j]["tMs"] - cl[-1]["tMs"]) / 1000 < gap:
            cl.append(bs[j]); j += 1
        nv = [c for c in cl if c["kind"] in NEWVOICE]
        if nv:
            t = nv[-1]["tMs"] / 1000.0
            strong = any(c["kind"] in ("speaker→speaker", "overlap-onset") for c in cl)
            marks.append(dict(t=t, strong=strong, n=len(cl)))
        i = j
    return marks
marks = consolidate(bnds)

# Interleave words and marks on a single timeline.
items = [("w", w["start"], w) for w in words] + [("m", m["t"], m) for m in marks]
items.sort(key=lambda x: x[1])

frag = []
for kind, t, obj in items:
    if kind == "w":
        frag.append(f'<span class=w data-s="{obj["start"]:.2f}" data-e="{obj["end"]:.2f}">{html.escape(obj["word"])}</span>')
    else:
        mm, ss = int(t // 60), int(t % 60)
        cls = "mark strong" if obj["strong"] else "mark"
        frag.append(f'<span class="{cls}" data-s="{t:.2f}" title="{obj["n"]} raw boundaries here">'
                    f'<span class=mt>{mm:02d}:{ss:02d}</span>⇋</span>')

doc = f"""<!doctype html><meta charset=utf-8>
<title>Transcript + speaker-change marks</title>
<style>
 body{{font:16px/1.9 Georgia,serif;margin:0;background:#fafaf8;color:#1a1a1a}}
 header{{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:10px 18px;z-index:5;font-family:system-ui}}
 h1{{font-size:14px;margin:0 0 6px}} .meta{{color:#888;font-size:12px;font-weight:400}}
 header audio{{width:100%;height:34px;margin-top:6px}}
 #t{{max-width:860px;margin:18px auto;padding:0 20px 60vh}}
 .w{{cursor:pointer;border-radius:3px}}
 .w:hover{{background:#eef}}
 .w.on{{background:#fde68a}}
 .mark{{display:inline-flex;align-items:center;gap:3px;margin:0 5px;padding:1px 6px;border-radius:10px;
        background:#e5e7eb;color:#6b7280;font:600 11px system-ui;cursor:pointer;vertical-align:middle;white-space:nowrap}}
 .mark.strong{{background:#fee2e2;color:#b91c1c}}
 .mark:hover{{outline:2px solid #93c5fd}}
 .mt{{font-variant-numeric:tabular-nums;opacity:.8}}
 .legend{{font-family:system-ui;font-size:12px;color:#666;margin-top:4px}}
 .legend b{{padding:1px 7px;border-radius:10px}}
</style>
<header>
 <h1>Transcript + speaker-change marks <span class=meta>— our Whisper words · ⇋ = segmentation says speaker changed · NO diarization/labels · click word or mark to play</span></h1>
 <div class=legend>
   <b style="background:#fee2e2;color:#b91c1c">⇋ strong</b> direct change / overlap &nbsp;
   <b style="background:#e5e7eb;color:#6b7280">⇋ pause</b> restart after silence &nbsp; · {len(marks)} marks, {len(words)} words
 </div>
 <audio id=a controls preload=auto src="{AUDIO}"></audio>
</header>
<div id=t>{' '.join(frag)}</div>
<script>
const a=document.getElementById('a'), ws=[...document.querySelectorAll('.w')];
function seek(t){{ if(a.readyState>=2){{a.currentTime=t;a.play();}} else {{a.addEventListener('canplay',()=>{{a.currentTime=t;a.play();}},{{once:true}});a.load();}} }}
document.querySelectorAll('[data-s]').forEach(el=>el.onclick=()=>seek(parseFloat(el.dataset.s)));
let last=null;
a.ontimeupdate=()=>{{
  const t=a.currentTime;
  let cur=null;
  for(const w of ws){{ if(t>=+w.dataset.s && t<+w.dataset.e){{cur=w;break;}} }}
  if(cur!==last){{ if(last)last.classList.remove('on'); if(cur){{cur.classList.add('on'); cur.scrollIntoView({{block:'center',behavior:'smooth'}});}} last=cur; }}
}};
</script>"""
open(os.path.join(S, "switchview.html"), "w").write(doc)
print(f"wrote switchview.html | {len(words)} words, {len(marks)} switch marks "
      f"({sum(1 for m in marks if m['strong'])} strong, {sum(1 for m in marks if not m['strong'])} pause)")
