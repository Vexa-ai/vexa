#!/usr/bin/env tsx
/**
 * bench-view — launchable side-by-side bench viewer with synced playback.
 *
 *   npm run bench:view [-- <bench-dir>]      # default ~/.vexa/fixtures/bench/podcast-520
 *
 * Serves a local page: the window audio + Deepgram (left) vs Vexa (right)
 * transcripts, same-speaker turns merged, colour-per-speaker. As the audio
 * plays both columns highlight the active turn (and auto-scroll); click any
 * turn to seek. This is how you SEE the diarization diverge — e.g. one
 * Deepgram speaker shredded into four Vexa clusters during a monologue.
 *
 * Reads from the bench dir: audio.wav (full meeting) + meta.json (window) +
 * ours.separated-transcript.v1.jsonl + reference.jsonl. Cuts window.wav once.
 */
import http from 'node:http';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { spawnSync } from 'node:child_process';

const PORT = Number(process.env.PORT || 8077);
const benchDir = process.argv[2] ||
  path.join(process.env.VEXA_FIXTURE_CACHE || path.join(os.homedir(), '.vexa', 'fixtures'), 'bench', 'podcast-520');
const FFMPEG = fs.existsSync(path.join(os.homedir(), 'bin', 'ffmpeg')) ? path.join(os.homedir(), 'bin', 'ffmpeg') : 'ffmpeg';

if (!fs.existsSync(path.join(benchDir, 'meta.json'))) { console.error(`[bench-view] no meta.json in ${benchDir}`); process.exit(1); }
const meta = JSON.parse(fs.readFileSync(path.join(benchDir, 'meta.json'), 'utf8'));
const win = meta.window || { start_s: 0, end_s: 0 };

// Cut the window-aligned audio once (transcripts are window-relative 0…N).
const winWav = path.join(benchDir, 'window.wav');
if (!fs.existsSync(winWav) || fs.statSync(winWav).size < 1000) {
  const full = path.join(benchDir, 'audio.wav');
  if (!fs.existsSync(full)) { console.error(`[bench-view] no audio.wav in ${benchDir}`); process.exit(1); }
  console.log(`[bench-view] cutting window.wav [${win.start_s}–${win.end_s}s]…`);
  const r = spawnSync(FFMPEG, ['-y', '-ss', String(win.start_s), '-t', String(win.end_s - win.start_s), '-i', full, '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', winWav], { stdio: 'ignore' });
  if (r.status !== 0) { console.error('[bench-view] ffmpeg cut failed'); process.exit(1); }
}

const readJsonl = (f: string) => fs.existsSync(path.join(benchDir, f))
  ? fs.readFileSync(path.join(benchDir, f), 'utf8').trim().split('\n').filter(Boolean).map((l) => JSON.parse(l))
  : [];
const ours = readJsonl('ours.separated-transcript.v1.jsonl').map((s: any) => ({ start: s.start, end: s.end, spk: String(s.speakerKey).replace('speaker_', 's'), text: s.text }));
const ref = readJsonl('reference.jsonl').map((s: any) => ({ start: s.start, end: s.end, spk: String(s.speaker).replace('dg-', 'R'), text: s.text }));

const PAGE = (data: string) => `<!doctype html><html><head><meta charset=utf8><title>bench-view ${meta.native_meeting_id}</title>
<style>
  :root{font-family:ui-sans-serif,system-ui,sans-serif}
  body{margin:0;background:#0b0d12;color:#e6e9ef}
  header{position:sticky;top:0;background:#11141b;border-bottom:1px solid #232838;padding:10px 16px;z-index:5}
  h1{font-size:14px;margin:0 0 6px;font-weight:600;color:#aab2c5}
  audio{width:100%;height:34px}
  .cols{display:grid;grid-template-columns:1fr 1fr;gap:0}
  .col{height:calc(100vh - 90px);overflow:auto;padding:8px 12px}
  .col.l{border-right:1px solid #232838}
  .lab{position:sticky;top:0;background:#0b0d12;font-size:12px;color:#7c869b;padding:4px 0;text-transform:uppercase;letter-spacing:.05em}
  .turn{padding:6px 9px;margin:5px 0;border-radius:7px;border-left:4px solid var(--c);background:#141823;cursor:pointer;line-height:1.4;font-size:13.5px}
  .turn:hover{background:#1b2030}
  .turn.active{background:#27314a;box-shadow:0 0 0 1px #4b6bdf}
  .spk{font-weight:700;color:var(--c);font-size:11px;margin-right:6px}
  .t{color:#5b647a;font-size:10.5px;float:right}
</style></head><body>
<header>
  <h1>bench-view — ${meta.native_meeting_id} · window ${Math.round(win.start_s/60)}:${String(Math.round(win.start_s)%60).padStart(2,'0')}–${Math.round(win.end_s/60)}:${String(Math.round(win.end_s)%60).padStart(2,'0')} · ${meta.window?.why||''}</h1>
  <audio id=a controls src="/audio.wav"></audio>
</header>
<div class=cols>
  <div class="col l"><div class=lab>Deepgram (reference)</div><div id=L></div></div>
  <div class="col r"><div class=lab>Vexa (ours)</div><div id=R></div></div>
</div>
<script>
const DATA=${data};
const PALETTE=['#5b8cff','#ff8a5b','#4fcf8f','#e05bff','#ffd24f','#ff5b7c','#4fd4e0','#9b8cff','#a0c64f','#ff9bd2'];
const colorOf={}; let ci=0;
function color(spk){ if(!(spk in colorOf)) colorOf[spk]=PALETTE[ci++%PALETTE.length]; return colorOf[spk]; }
function merge(segs){ const o=[]; for(const s of segs){ const p=o[o.length-1]; if(p&&p.spk===s.spk){p.end=s.end;p.text+=' '+s.text;} else o.push({...s}); } return o; }
function fmt(t){ return Math.floor(t/60)+':'+String(Math.floor(t%60)).padStart(2,'0'); }
function render(el,segs){ const m=merge(segs); el._turns=[]; m.forEach(s=>{ const d=document.createElement('div'); d.className='turn'; d.style.setProperty('--c',color(s.spk)); d.innerHTML='<span class=spk>'+s.spk+'</span><span class=t>'+fmt(s.start)+'</span>'+s.text.replace(/</g,'&lt;'); d.onclick=()=>{a.currentTime=s.start;a.play();}; d._s=s.start; d._e=s.end; el.appendChild(d); el._turns.push(d); }); }
const a=document.getElementById('a');
render(document.getElementById('L'),DATA.ref);
render(document.getElementById('R'),DATA.ours);
function sync(){ const t=a.currentTime; for(const el of [document.getElementById('L'),document.getElementById('R')]){ let hit=null; for(const d of el._turns){ const on=t>=d._s&&t<d._e; if(on&&!d.classList.contains('active')){d.classList.add('active');hit=d;} else if(!on) d.classList.remove('active'); } if(hit) hit.scrollIntoView({block:'center',behavior:'smooth'}); } }
a.addEventListener('timeupdate',sync);
</script></body></html>`;

const server = http.createServer((req, res) => {
  const url = (req.url || '/').split('?')[0];
  if (url === '/' || url === '/index.html') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    res.end(PAGE(JSON.stringify({ ours, ref })));
    return;
  }
  if (url === '/audio.wav') {
    const stat = fs.statSync(winWav);
    const range = req.headers.range;
    if (range) {
      const m = /bytes=(\d+)-(\d*)/.exec(range);
      const start = m ? parseInt(m[1], 10) : 0;
      const end = m && m[2] ? parseInt(m[2], 10) : stat.size - 1;
      res.writeHead(206, { 'content-type': 'audio/wav', 'accept-ranges': 'bytes', 'content-range': `bytes ${start}-${end}/${stat.size}`, 'content-length': end - start + 1 });
      fs.createReadStream(winWav, { start, end }).pipe(res);
    } else {
      res.writeHead(200, { 'content-type': 'audio/wav', 'accept-ranges': 'bytes', 'content-length': stat.size });
      fs.createReadStream(winWav).pipe(res);
    }
    return;
  }
  res.writeHead(404).end();
});
server.listen(PORT, () => {
  console.log(`[bench-view] ${benchDir}`);
  console.log(`[bench-view] Deepgram ${ref.length} segs · Vexa ${ours.length} segs`);
  console.log(`[bench-view] ▶ open  http://localhost:${PORT}`);
});
