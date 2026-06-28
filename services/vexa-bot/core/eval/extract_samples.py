#!/usr/bin/env python3
"""Extract AMI samples for diarization-eval RnD: mixed-headset WAV + ground-truth
diarization (start,end,speaker) + joined word transcripts + an HTML viewer.

Audio + speaker turns from diarizers-community/ami (ihm, Mix-Headset).
Transcript text joined from edinburghcstr/ami (ihm) by meeting id + time overlap.
"""
import datasets, soundfile as sf, io, numpy as np, json, os, html

OUT = os.path.join(os.path.dirname(__file__), "samples")
os.makedirs(OUT, exist_ok=True)
PICKS = {"EN2002d", "IS1009a", "TS3003d"}

print("[1/3] streaming diarizers-community/ami (audio + speaker turns)...", flush=True)
dds = datasets.load_dataset("diarizers-community/ami", "ihm", split="test", streaming=True)
dds = dds.cast_column("audio", datasets.Audio(decode=False))

meetings = {}  # mid -> dict(wav_path, turns=[(s,e,spk)], sr, dur)
for ex in dds:
    path = ex["audio"]["path"]                # e.g. EN2002d.Mix-Headset.wav
    mid = path.split(".")[0]
    if mid not in PICKS or mid in meetings:
        continue
    b = ex["audio"]["bytes"]
    arr, sr = sf.read(io.BytesIO(b), dtype="float32")
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    wav_path = os.path.join(OUT, f"{mid}.wav")
    sf.write(wav_path, arr, sr, subtype="PCM_16")
    turns = list(zip(ex["timestamps_start"], ex["timestamps_end"], ex["speakers"]))
    turns.sort(key=lambda t: t[0])
    meetings[mid] = dict(wav=wav_path, turns=turns, sr=sr, dur=len(arr) / sr)
    print(f"    saved {mid}: {len(arr)/sr:.0f}s @ {sr}Hz, {len(turns)} turns -> {wav_path}", flush=True)
    if len(meetings) == len(PICKS):
        break

# ground-truth diarization RTTM + JSON
for mid, m in meetings.items():
    with open(os.path.join(OUT, f"{mid}.gt.rttm"), "w") as f:
        for s, e, spk in m["turns"]:
            f.write(f"SPEAKER {mid} 1 {s:.3f} {e-s:.3f} <NA> <NA> {spk} <NA> <NA>\n")
    json.dump([dict(start=round(s, 3), end=round(e, 3), speaker=spk) for s, e, spk in m["turns"]],
              open(os.path.join(OUT, f"{mid}.gt.json"), "w"), indent=2)

print("[2/3] streaming edinburghcstr/ami (transcript text)...", flush=True)
# edinburghcstr/ami ihm has per-segment text with meeting_id, begin_time, end_time, speaker_id, text
text_by_mid = {mid: [] for mid in meetings}
try:
    tds = datasets.load_dataset("edinburghcstr/ami", "ihm", split="test", streaming=True, trust_remote_code=True)
    for ex in tds:
        mid = ex.get("meeting_id") or ex.get("session_id") or ""
        if mid in text_by_mid:
            txt = (ex.get("text") or "").strip()
            if txt:
                text_by_mid[mid].append(dict(
                    start=float(ex.get("begin_time", ex.get("start_time", 0)) or 0),
                    end=float(ex.get("end_time", 0) or 0),
                    speaker=ex.get("speaker_id", ex.get("speaker", "")),
                    text=txt,
                ))
except Exception as e:
    print(f"    [warn] edinburghcstr/ami text pull failed: {e!r}", flush=True)
    print("    transcripts will be empty; diarization GT is still complete.", flush=True)

for mid, segs in text_by_mid.items():
    segs.sort(key=lambda s: s["start"])
    json.dump(segs, open(os.path.join(OUT, f"{mid}.transcript.json"), "w"), indent=2)
    print(f"    {mid}: {len(segs)} transcript segments", flush=True)

print("[3/3] building viewer.html...", flush=True)
# Build a per-meeting transcript view: merge GT turns with overlapping transcript text.
def build_rows(mid, m, segs):
    rows = []
    for s, e, spk in m["turns"]:
        # find transcript segments overlapping this turn from same speaker
        words = [g["text"] for g in segs
                 if g["speaker"] == spk and not (g["end"] < s or g["start"] > e)]
        rows.append((s, e, spk, " ".join(words)))
    return rows

cards = []
for mid, m in meetings.items():
    segs = text_by_mid.get(mid, [])
    rows = build_rows(mid, m, segs)
    tr = "\n".join(
        f'<tr data-start="{s:.2f}"><td class=t>{int(s//60):02d}:{int(s%60):02d}</td>'
        f'<td class=s spk-{html.escape(spk)}>{html.escape(spk)}</td>'
        f'<td>{html.escape(txt) or "<i>(no text)</i>"}</td></tr>'
        for s, e, spk, txt in rows)
    cards.append(f"""
<section>
  <h2>{html.escape(mid)} <small>{m['dur']:.0f}s · {len(m['turns'])} turns · {len(set(t[2] for t in m['turns']))} speakers</small></h2>
  <audio controls preload=none src="{mid}.wav" id="a-{mid}"></audio>
  <div class=scroll><table>
    <thead><tr><th>t</th><th>spk</th><th>transcript (GT speaker + AMI words)</th></tr></thead>
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
 .scroll{{max-height:420px;overflow:auto;border:1px solid #eee}}
 table{{border-collapse:collapse;width:100%}}
 td,th{{padding:3px 8px;border-bottom:1px solid #f0f0f0;vertical-align:top;text-align:left}}
 td.t{{color:#999;font-variant-numeric:tabular-nums;white-space:nowrap;cursor:pointer}}
 td.s{{font-weight:600;white-space:nowrap}}
 tr:hover{{background:#fafafa}}
</style>
<h1>AMI diarization-eval samples</h1>
<p>Click a timestamp to seek the audio. Speaker labels are AMI ground truth; words joined from the AMI manual transcript.</p>
{''.join(cards)}
<script>
document.querySelectorAll('td.t').forEach(td=>td.onclick=()=>{{
  const sec=parseFloat(td.parentElement.dataset.start);
  const a=td.closest('section').querySelector('audio');
  a.currentTime=sec; a.play();
}});
</script>"""
open(os.path.join(OUT, "viewer.html"), "w").write(doc)
print(f"    wrote {os.path.join(OUT, 'viewer.html')}", flush=True)
print("DONE.", flush=True)
