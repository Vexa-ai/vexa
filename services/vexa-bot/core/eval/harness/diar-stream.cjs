const { readFileSync } = require('fs');
const WebSocket = require('/home/dima/dev/vexa/services/vexa-bot/node_modules/ws');
const TOK = process.argv[2];
const buf = readFileSync('/tmp/diartest.f32');
const f32 = new Float32Array(buf.buffer, buf.byteOffset, buf.length / 4);
const ws = new WebSocket(`ws://localhost:8092/ingest?platform=zoom&native_meeting_id=diartest1&api_key=${TOK}&language=en`);
const CHUNK = 320;
let i = 0;
ws.on('message', (m) => { try { const d = JSON.parse(m.toString()); if (d.type === 'ready') { console.log('READY meeting', d.meeting_id); pump(); } } catch {} });
ws.on('error', e => { console.log('WS ERR', e.message); process.exit(1); });
function pump() {
  const t = setInterval(() => {
    if (i >= f32.length) { clearInterval(t); console.log('STREAM DONE @', (i/16000).toFixed(0),'s'); setTimeout(()=>{ws.close(1000);process.exit(0);}, 10000); return; }
    const slice = f32.subarray(i, i + CHUNK); i += CHUNK;
    const ab = new ArrayBuffer(4 + slice.length * 4);
    new DataView(ab).setInt32(0, 999, true);
    new Float32Array(ab, 4).set(slice);
    ws.send(ab);
  }, 20);
}
