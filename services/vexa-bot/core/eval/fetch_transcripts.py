#!/usr/bin/env python3
"""Fill in word-level transcripts from the AMI manual annotations (XML, no audio
decode needed), then rebuild viewer.html with text. Joins words to GT speaker
turns by AMI speaker channel (A/B/C/D) + time overlap.
"""
import os, io, zipfile, urllib.request, xml.etree.ElementTree as ET, json, glob, html, re

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "samples")
ZIP_URL = "https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip"
ZIP_PATH = os.path.join(HERE, "ami_manual.zip")
PICKS = ["EN2002d", "IS1009a", "TS3003d"]

if not os.path.exists(ZIP_PATH):
    print(f"downloading {ZIP_URL} ...", flush=True)
    urllib.request.urlretrieve(ZIP_URL, ZIP_PATH)
print(f"zip: {os.path.getsize(ZIP_PATH)/1e6:.1f} MB", flush=True)

# AMI word files: words/<MID>.<CH>.words.xml  CH in A,B,C,D
# Each <w starttime= endtime=>text</w>; <w> may be punctuation (punc="true").
zf = zipfile.ZipFile(ZIP_PATH)
names = zf.namelist()

def words_for(mid):
    out = {}  # channel -> [(start,end,text)]
    for ch in "ABCDE":
        cand = f"words/{mid}.{ch}.words.xml"
        if cand not in names:
            continue
        root = ET.fromstring(zf.read(cand))
        ws = []
        for w in root.iter():
            tag = w.tag.split("}")[-1]
            if tag != "w":
                continue
            st = w.get("starttime"); et = w.get("endtime")
            txt = (w.text or "").strip()
            if st is None or et is None or not txt:
                continue
            ws.append((float(st), float(et), txt))
        ws.sort()
        out[ch] = ws
    return out

# Need to map GT speaker labels (e.g. FEO072) -> channel (A/B/C/D) per meeting.
# AMI corpusResources/meetings.xml maps speaker global-name + channel; but the
# simplest robust join is by TIME against the diarization GT turns which already
# carry the speaker label — we instead emit per-channel transcript and let the
# viewer join by time overlap with GT turns regardless of label name.
for mid in PICKS:
    chans = words_for(mid)
    # Build flat list of (start,end,channel,text) words
    flat = []
    for ch, ws in chans.items():
        for st, et, txt in ws:
            flat.append(dict(start=round(st, 3), end=round(et, 3), channel=ch, text=txt))
    flat.sort(key=lambda x: x["start"])
    json.dump(flat, open(os.path.join(OUT, f"{mid}.words.json"), "w"))
    print(f"{mid}: {len(flat)} words across channels {sorted(chans)}", flush=True)

# Rebuild viewer: for each GT turn, gather words whose [start,end] overlaps the
# turn window (any channel) — gives readable transcript aligned to GT speaker.
gt = {mid: json.load(open(os.path.join(OUT, f"{mid}.gt.json"))) for mid in PICKS}
words = {mid: json.load(open(os.path.join(OUT, f"{mid}.words.json"))) for mid in PICKS}

# Map GT speaker -> dominant channel by maximal word-time overlap, so each turn
# pulls words from the right channel.
def turn_text(turn, ws):
    s, e = turn["start"], turn["end"]
    chosen = [w["text"] for w in ws if not (w["end"] < s or w["start"] > e)]
    return " ".join(chosen)

cards = []
for mid in PICKS:
    turns = gt[mid]
    ws = words[mid]
    # index words by coarse second bucket for speed
    rows = []
    for t in turns:
        rows.append((t["start"], t["end"], t["speaker"], turn_text(t, ws)))
    dur = max((t["end"] for t in turns), default=0)
    nspk = len(set(t["speaker"] for t in turns))
    tr = "\n".join(
        f'<tr data-start="{s:.2f}"><td class=t>{int(s//60):02d}:{int(s%60):02d}</td>'
        f'<td class=s>{html.escape(spk)}</td>'
        f'<td>{html.escape(txt) or "<i>(silence/no-word)</i>"}</td></tr>'
        for s, e, spk, txt in rows)
    cards.append(f"""
<section>
  <h2>{html.escape(mid)} <small>{dur:.0f}s · {len(turns)} turns · {nspk} speakers</small></h2>
  <audio controls preload=none src="{mid}.wav" id="a-{mid}"></audio>
  <div class=scroll><table>
    <thead><tr><th>t</th><th>spk</th><th>transcript (AMI manual words)</th></tr></thead>
    <tbody>{tr}</tbody>
  </table></div>
</section>""")

doc = f"""<!doctype html><meta charset=utf-8>
<title>AMI diarization-eval samples</title>
<style>
 body{{font:14px/1.5 system-ui;margin:24px;max-width:1100px}}
 section{{margin:32px 0;border-top:2px solid #ccc;padding-top:12px}}
 h2 small{{font-weight:400;color:#777;font-size:13px}}
 audio{{width:100%;margin:8px 0}}
 .scroll{{max-height:440px;overflow:auto;border:1px solid #eee}}
 table{{border-collapse:collapse;width:100%}}
 td,th{{padding:3px 8px;border-bottom:1px solid #f0f0f0;vertical-align:top;text-align:left}}
 td.t{{color:#999;font-variant-numeric:tabular-nums;white-space:nowrap;cursor:pointer}}
 td.s{{font-weight:600;white-space:nowrap}}
 tr:hover{{background:#fafafa}}
</style>
<h1>AMI diarization-eval samples</h1>
<p>Click a timestamp to seek + play. Speaker labels are AMI ground truth; words from the AMI manual transcript joined by time overlap.</p>
{''.join(cards)}
<script>
document.querySelectorAll('td.t').forEach(td=>td.onclick=()=>{{
  const sec=parseFloat(td.parentElement.dataset.start);
  const a=td.closest('section').querySelector('audio');
  a.currentTime=sec; a.play();
}});
</script>"""
open(os.path.join(OUT, "viewer.html"), "w").write(doc)
print("rebuilt viewer.html with transcripts. DONE.", flush=True)
