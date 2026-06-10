#!/usr/bin/env python3
"""Ear-validation of the SEGMENTATION model only (pyannote/segmentation-3.0).
No wespeaker, no clustering — just the raw speaker-change boundaries. For each
boundary, play the +/-3s window with a red line + audible tick AT the detected
border, so you can judge by ear whether the voice actually changes there.
"""
import json, os, html
HERE = os.path.dirname(__file__); S = os.path.join(HERE, "samples")
AUDIO = "dgclip.wav"; WIN = 3.0  # seconds each side

d = json.load(open(os.path.join(S, "dgclip_bnd.boundaries.json")))
bnds = sorted(d["boundaries"], key=lambda x: x["tMs"])

KIND_COLOR = {
    "speaker→speaker": "#dc2626",   # direct change — the real test
    "silence→speaker": "#2563eb",   # someone starts
    "speaker→silence": "#9ca3af",   # someone stops
    "overlap-onset":   "#d97706",
    "overlap-offset":  "#b45309",
}

rows = []
for i, b in enumerate(bnds):
    t = b["tMs"] / 1000.0
    mm, ss = int(t // 60), t % 60
    c = KIND_COLOR.get(b["kind"], "#333")
    rows.append(
        f'<div class=row data-t="{t:.3f}" data-i="{i}">'
        f'<span class=idx>{i+1}</span>'
        f'<span class=ts>{mm:02d}:{ss:05.2f}</span>'
        f'<span class=kind style="background:{c}">{html.escape(b["kind"])}</span>'
        f'<span class=conf>conf {b["confidence"]:.2f}</span>'
        f'<span class=track><span class=line></span><span class=play>▶ play ±{WIN:.0f}s</span></span>'
        f'</div>')

doc = f"""<!doctype html><meta charset=utf-8>
<title>Segmentation ear-test — pyannote boundaries</title>
<style>
 body{{font:13px/1.5 system-ui;margin:0;background:#0f172a;color:#e2e8f0}}
 header{{position:sticky;top:0;background:#1e293b;border-bottom:1px solid #334155;padding:10px 16px;z-index:5}}
 h1{{font-size:15px;margin:0 0 4px}} .meta{{color:#94a3b8;font-size:12px}}
 header audio{{width:100%;height:34px;margin-top:6px}}
 .legend{{margin-top:6px;font-size:11px}} .legend b{{padding:1px 6px;border-radius:4px;color:#fff;margin-right:6px}}
 .row{{display:flex;align-items:center;gap:10px;padding:5px 14px;border-bottom:1px solid #1e293b;cursor:pointer}}
 .row:hover{{background:#1e293b}} .row.active{{background:#334155}}
 .idx{{color:#64748b;width:30px;text-align:right;font-variant-numeric:tabular-nums}}
 .ts{{color:#cbd5e1;width:62px;font-variant-numeric:tabular-nums;font-weight:600}}
 .kind{{color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:5px;width:130px;text-align:center}}
 .conf{{color:#64748b;width:64px;font-size:11px}}
 .track{{position:relative;flex:1;height:26px;background:#0b1220;border-radius:5px;min-width:240px;overflow:hidden}}
 .line{{position:absolute;left:50%;top:0;bottom:0;width:2px;background:#ef4444}}
 .play{{position:absolute;left:8px;top:5px;font-size:11px;color:#94a3b8}}
 .head{{position:absolute;top:0;bottom:0;width:2px;background:#22c55e;left:0;display:none}}
 .row.active .head{{display:block}}
</style>
<header>
 <h1>Segmentation ear-test <span class=meta>— pyannote/segmentation-3.0 ONLY (no wespeaker, no clustering) · {len(bnds)} boundaries</span></h1>
 <div class=meta>Click a boundary → plays the audio from 3s before to 3s after. The <b style="color:#ef4444">red line</b> + a <b style="color:#22c55e">tick sound</b> mark the EXACT detected border. Validate: does the voice change right at the line?</div>
 <div class=legend>
   <b style="background:#dc2626">speaker→speaker</b> direct change
   <b style="background:#2563eb">silence→speaker</b> starts
   <b style="background:#9ca3af">speaker→silence</b> stops
   <b style="background:#d97706">overlap</b> onset/offset
 </div>
 <audio id=a controls preload=auto src="{AUDIO}"></audio>
</header>
<div id=list>
{chr(10).join(rows)}
</div>
<script>
const a=document.getElementById('a'), WIN={WIN};
let actx=null, cur=null, raf=null, ticked=false;
function tick(){{
  try{{ actx=actx||new (window.AudioContext||window.webkitAudioContext)();
    const o=actx.createOscillator(), g=actx.createGain();
    o.frequency.value=1100; o.connect(g); g.connect(actx.destination);
    g.gain.setValueAtTime(0.001,actx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.25,actx.currentTime+0.005);
    g.gain.exponentialRampToValueAtTime(0.001,actx.currentTime+0.07);
    o.start(); o.stop(actx.currentTime+0.08);
  }}catch(e){{}}
}}
function stop(){{ if(raf)cancelAnimationFrame(raf); raf=null; a.pause();
  if(cur){{cur.classList.remove('active'); const h=cur.querySelector('.head'); if(h)h.remove();}} cur=null; }}
function loop(T,row,head){{
  const lo=T-WIN, hi=T+WIN, p=(a.currentTime-lo)/(2*WIN);
  head.style.left=Math.max(0,Math.min(1,p))*100+'%';
  if(!ticked && a.currentTime>=T){{ ticked=true; tick(); }}
  if(a.currentTime>=hi){{ stop(); return; }}
  raf=requestAnimationFrame(()=>loop(T,row,head));
}}
function playRow(row){{
  stop(); const T=parseFloat(row.dataset.t); ticked=false; cur=row; row.classList.add('active');
  let head=row.querySelector('.head'); if(!head){{head=document.createElement('span');head.className='head';row.querySelector('.track').appendChild(head);}}
  const go=()=>{{ a.currentTime=Math.max(0,T-WIN); a.play(); raf=requestAnimationFrame(()=>loop(T,row,head)); }};
  if(a.readyState>=2) go(); else {{ a.addEventListener('canplay',go,{{once:true}}); a.load(); }}
}}
document.querySelectorAll('.row').forEach(r=>r.onclick=()=>playRow(r));
</script>"""
open(os.path.join(S, "segtest.html"), "w").write(doc)
print(f"wrote segtest.html | {len(bnds)} boundaries from pyannote/segmentation-3.0 only")
