#!/usr/bin/env python3
"""CUT-AT-SWITCH viewer with WORD-level playback. Each block is ONE buffer (the
audio between two speaker-change signals), Whispered in isolation. A divider
with ⇋ marks each cut. Click any WORD to play from there; the current word
highlights as it plays. NO diarization, NO speaker labels.
Usage: build_cutview.py <segments.json> <audio.wav> <out.html>
"""
import json, os, html, sys
HERE = os.path.dirname(__file__); S = os.path.join(HERE, "samples")
segfile = sys.argv[1] if len(sys.argv) > 1 else os.path.join(S, "dgclip.segments.json")
AUDIO = sys.argv[2] if len(sys.argv) > 2 else "dgclip.wav"
outfile = sys.argv[3] if len(sys.argv) > 3 else os.path.join(S, "cutview.html")

d = json.load(open(segfile)); segs = d["segments"]

def words_html(seg):
    ws = seg.get("words") or []
    if not ws:
        return f'<span class=w data-s="{seg["start"]:.2f}" data-e="{seg["end"]:.2f}">{html.escape(seg.get("text","")) or "<i>(no speech)</i>"}</span>'
    return " ".join(f'<span class=w data-s="{w["start"]:.2f}" data-e="{w["end"]:.2f}">{html.escape(w["word"])}</span>' for w in ws)

blocks = []
for i, s in enumerate(segs):
    mm, ss = int(s["start"] // 60), int(s["start"] % 60)
    strong = s.get("startedByStrongSwitch")
    if i == 0:
        mark = f'<div class="cut start"><span class=ct>{mm:02d}:{ss:02d}</span> ▶ start</div>'
    else:
        mark = (f'<div class="cut {"strong" if strong else "pause"}">'
                f'<span class=ct>{mm:02d}:{ss:02d}</span> ⇋ {"speaker change" if strong else "restart after pause"}'
                f'<span class=dur>{s["durS"]:.1f}s buffer</span></div>')
    blocks.append(mark + f'<div class=seg data-s="{s["start"]:.2f}" data-e="{s["end"]:.2f}">{words_html(s)}</div>')

doc = f"""<!doctype html><meta charset=utf-8>
<title>Cut-at-switch transcript (word-level)</title>
<style>
 body{{font:16px/1.8 Georgia,serif;margin:0;background:#fafaf8;color:#1a1a1a}}
 header{{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:10px 18px;z-index:5;font-family:system-ui}}
 h1{{font-size:14px;margin:0 0 6px}} .meta{{color:#888;font-size:12px;font-weight:400}}
 header audio{{width:100%;height:34px;margin-top:6px}}
 #t{{max-width:820px;margin:14px auto;padding:0 20px 60vh}}
 .cut{{display:flex;align-items:center;gap:8px;font:600 11px system-ui;margin:14px 0 4px;color:#6b7280}}
 .cut .ct{{font-variant-numeric:tabular-nums}} .cut .dur{{margin-left:auto;color:#b0b0b0;font-weight:400}}
 .cut.strong{{color:#b91c1c}} .cut.start{{color:#059669}}
 .cut::before{{content:"";flex:0 0 3px;align-self:stretch;background:currentColor;border-radius:2px;min-height:16px}}
 .seg{{padding:4px 10px;border-radius:6px;border-left:3px solid #e5e7eb}}
 .seg.on{{border-left-color:#d97706;background:#fffdf5}}
 .w{{cursor:pointer;border-radius:3px;padding:0 1px}}
 .w:hover{{background:#dbeafe}} .w.on{{background:#fde68a}}
</style>
<header>
 <h1>Cut-at-switch transcript — word-level <span class=meta>— each block = one buffer between two switch signals, Whispered ALONE · click any WORD to play · NO diarization · {len(segs)} buffers</span></h1>
 <div class=meta><b style="color:#b91c1c">⇋ speaker change</b> · <b style="color:#6b7280">⇋ restart after pause</b> · <b style="color:#059669">▶ start</b></div>
 <audio id=a controls preload=auto src="{AUDIO}"></audio>
</header>
<div id=t>{''.join(blocks)}</div>
<script>
const a=document.getElementById('a'), ws=[...document.querySelectorAll('.w')], segs=[...document.querySelectorAll('.seg')];
function seek(t){{ if(a.readyState>=2){{a.currentTime=t;a.play();}} else {{a.addEventListener('canplay',()=>{{a.currentTime=t;a.play();}},{{once:true}});a.load();}} }}
document.querySelectorAll('[data-s]').forEach(el=>el.onclick=e=>{{e.stopPropagation();seek(parseFloat(el.dataset.s));}});
let lw=null, lsg=null;
a.ontimeupdate=()=>{{ const t=a.currentTime;
  let cw=null; for(const w of ws){{ if(t>=+w.dataset.s && t<+w.dataset.e){{cw=w;break;}} }}
  if(cw!==lw){{ if(lw)lw.classList.remove('on'); if(cw){{cw.classList.add('on'); cw.scrollIntoView({{block:'center',behavior:'smooth'}});}} lw=cw; }}
  let cs=null; for(const sg of segs){{ if(t>=+sg.dataset.s && t<+sg.dataset.e){{cs=sg;break;}} }}
  if(cs!==lsg){{ if(lsg)lsg.classList.remove('on'); if(cs)cs.classList.add('on'); lsg=cs; }}
}};
</script>"""
open(outfile, "w").write(doc)
print(f"wrote {os.path.basename(outfile)} | {len(segs)} buffers, word-level playback, audio={AUDIO}")
