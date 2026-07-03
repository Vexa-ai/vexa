"""web — a one-page control panel to actually USE the cookbook from a browser.

    python -m vexa_slim.web            # http://127.0.0.1:8800

Four tabs, all driven ONLY by `cookbook.*` (a working click IS a cookbook proof):
  · Chat / Onboard  — talk to your agent; "Start onboarding" runs the cold-start interview
  · Workspace       — your kg/entities, click to read
  · Meeting         — paste a Meet URL → send bot → live notes + cards
  · Routines        — list + schedule

Auth: reads VEXA_API_KEY from clients/terminal/.env.local (run `python -m vexa_slim.play login …` once).
"""
from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

from . import client as _client

_client.AgentApi.PREFIX = "/api"  # match the deployed gateway contract

from . import cookbook as cb  # noqa: E402
from .client import Slim  # noqa: E402
from .config import api_key, gateway_url, load_env  # noqa: E402
from .harvest import harvest  # noqa: E402

load_env()
_SLIM = Slim(gateway_url(), api_key())

_PAGE = r"""<!doctype html><meta charset=utf-8><title>Vexa · control panel</title>
<style>
 :root{--bg:#0f1115;--panel:#171a21;--line:#262b36;--mut:#9aa4b2;--accent:#2a6}
 *{box-sizing:border-box} body{font:14px/1.5 system-ui,sans-serif;margin:0;background:var(--bg);color:#e6e6e6}
 header{display:flex;gap:14px;align-items:center;padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--line)}
 header b{font-size:16px} #who{color:var(--mut);margin-left:auto;font-size:13px}
 nav{display:flex;gap:6px;padding:8px 16px;background:#12141a;border-bottom:1px solid var(--line)}
 nav button{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid var(--line);background:#0f1115;color:#cfd6e0;cursor:pointer}
 nav button.on{background:var(--accent);border-color:var(--accent);color:#04210f;font-weight:600}
 main{padding:16px;max-width:1100px;margin:0 auto}
 .tab{display:none} .tab.on{display:block}
 input,textarea,button.act{font:inherit;padding:8px 10px;border-radius:6px;border:1px solid #333;background:#0f1115;color:#e6e6e6}
 button.act{background:var(--accent);border-color:var(--accent);color:#04210f;cursor:pointer;font-weight:600}
 .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
 .panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;margin:10px 0}
 h2{margin:0 0 8px;font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
 #log{height:48vh;overflow:auto;display:flex;flex-direction:column;gap:8px;padding:4px}
 .msg{padding:8px 10px;border-radius:8px;max-width:80%}
 .me{align-self:flex-end;background:#1c3a52} .ag{align-self:flex-start;background:#10141b;border:1px solid #2a3140}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
 .note{padding:6px 8px;border-bottom:1px solid #20242e} .spk{color:#7fb2ff;font-weight:600}
 .card{padding:8px;margin:6px 0;background:#10141b;border:1px solid #2a3140;border-radius:6px}
 .kind{font-size:11px;color:#04210f;background:var(--accent);border-radius:4px;padding:1px 6px;margin-right:6px}
 .ent{padding:6px 8px;cursor:pointer;border-bottom:1px solid #20242e} .ent:hover{background:#10141b}
 pre{white-space:pre-wrap;background:#0d0f14;border:1px solid var(--line);border-radius:6px;padding:10px;max-height:50vh;overflow:auto}
 .mut{color:var(--mut)} .counts{color:var(--mut);margin-left:auto}
</style>
<header><b>Vexa</b> <span class=mut>control panel</span><span id=who></span></header>
<nav>
 <button class="on" onclick="tab('chat',this)">Chat / Onboard</button>
 <button onclick="tab('ws',this)">Workspace</button>
 <button onclick="tab('meet',this)">Meeting</button>
 <button onclick="tab('routines',this)">Routines</button>
</nav>
<main>
 <section id=chat class="tab on">
   <div class=panel>
     <div class=row><button class=act onclick=onboard()>Start onboarding</button>
       <input id=session value=main size=10 title="session id"><span class=mut>session</span></div>
     <div id=log></div>
     <div class=row><input id=msg placeholder="type a message…" style="flex:1" onkeydown="if(event.key=='Enter')send()">
       <button class=act onclick=send()>send</button></div>
   </div>
 </section>
 <section id=ws class="tab">
   <div class=grid>
     <div class=panel><h2>Entities (kg/entities)</h2><button class=act onclick=browse()>refresh</button><div id=ents></div></div>
     <div class=panel><h2>File</h2><pre id=filebody>click an entity…</pre></div>
   </div>
 </section>
 <section id=meet class="tab">
   <div class=panel><div class=row>
     <input id=url placeholder="https://meet.google.com/abc-defg-hij" style="flex:1">
     <button class=act onclick=sendbot()>send bot + watch</button><span id=mstatus class=mut></span><span class=counts id=counts></span>
   </div>
   <div id=pipeline class=row style="margin-top:8px;gap:14px;font-size:13px"></div></div>
   <div class=grid>
     <div class=panel><h2>Notes</h2><div id=notes class=mut>—</div></div>
     <div class=panel><h2>Cards</h2><div id=cards class=mut>—</div></div>
   </div>
 </section>
 <section id=routines class="tab">
   <div class=panel><h2>Schedule a routine</h2><div class=row>
     <input id=rname placeholder="name (daily-graph)"><input id=rcron placeholder='cron (0 18 * * *)' size=14>
     <input id=rprompt placeholder="what it does each run" style="flex:1">
     <button class=act onclick=addroutine()>schedule</button></div></div>
   <div class=panel><h2>Routines</h2><button class=act onclick=routines()>refresh</button><div id=rlist></div></div>
 </section>
</main>
<script>
const $=id=>document.getElementById(id);
function tab(n,b){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));$(n).classList.add('on');
  document.querySelectorAll('nav button').forEach(x=>x.classList.remove('on'));b.classList.add('on');}
async function j(u,o){const r=await fetch(u,o);return r.json();}

// CHAT
let onboarding=false;
function add(t,who){const d=document.createElement('div');d.className='msg '+who;d.textContent=t;$('log').appendChild(d);$('log').scrollTop=1e9;}
function onboard(){  // deterministic: show the greeting instantly, seed the workspace; the AGENT engages on your answer
  add("Hey — let's scaffold your workspace. What's your LinkedIn? (paste the profile text, not the URL — it's login-walled.)",'ag');
  fetch('/init').catch(()=>{});
  onboarding=true;}
async function send(){const m=$('msg').value.trim();if(!m)return;$('msg').value='';add(m,'me');
  const body={msg:m,session:$('session').value};
  if(onboarding){body.files=['onboarding.md'];onboarding=false;}  // first real turn loads the playbook
  add('…','ag');const ph=$('log').lastChild;
  const d=await j('/chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
  ph.textContent=d.reply||('[err] '+(d.error||''));$('log').scrollTop=1e9;}

// WORKSPACE
async function browse(){const d=await j('/browse');$('ents').innerHTML=(d.entities||[]).map(p=>`<div class=ent onclick="readf('${p}')">${p}</div>`).join('')||'<i class=mut>none yet — onboard first</i>';}
async function readf(p){const d=await j('/readfile?path='+encodeURIComponent(p));$('filebody').textContent=d.content||d.error||'(empty)';}

// MEETING
let timer=null;function nativeOf(u){const m=u.match(/[a-z]{3}-[a-z]{4}-[a-z]{3}/i);return m?m[0]:u.trim();}
async function sendbot(){const u=$('url').value.trim();if(!u)return;const n=nativeOf(u);$('mstatus').textContent='sending bot to '+n+'…';
  const d=await j('/sendbot?url='+encodeURIComponent(u)+'&native='+encodeURIComponent(n));
  $('mstatus').textContent=d.ok?('bot live on '+n+' — watching'):('err: '+d.error);
  if(timer)clearInterval(timer);poll(n);timer=setInterval(()=>poll(n),4000);}
function dot(ok,label,detail){const c=ok?'#2a6':'#e0533a';return `<span title="${(detail||'').replace(/"/g,'')}"><span style="color:${c}">●</span> ${label}</span>`;}
async function pipeline(d){  // P18: light each hop; the stuck one goes red with the typed reason
  let h={}; try{h=await j('/relay')}catch(e){}
  const ing=h.ingest||{}, nr=h.native_resolve||{};
  const counts=d.counts||{}, hasTx=(counts.transcript||0)>0, hasOut=((counts.note||0)+(counts.card||0))>0;
  const relayOk = nr.ok!==false;
  const els=[
    dot(true,'bot',''),
    dot((ing.segments||0)>0,'transcribing', (ing.segments||0)+' segments'),
    dot(relayOk,'relaying', relayOk?'ok':((nr.kind||'')+': '+(nr.detail||''))),
    dot(hasOut,'copilot', hasOut?'notes+cards':'waiting'),
  ];
  $('pipeline').innerHTML = els.join('') + (relayOk?'':` <span style="color:#e0533a">— ${nr.detail||'relay stalled'}</span>`);
}
async function poll(n){const d=await j('/snapshot?native='+encodeURIComponent(n));$('counts').textContent=JSON.stringify(d.counts||{});
  $('notes').innerHTML=(d.notes||[]).map(x=>`<div class=note><span class=spk>${x.speaker||''}</span> ${x.text||''}</div>`).join('')||'<i>waiting…</i>';
  $('cards').innerHTML=(d.cards||[]).map(c=>`<div class=card><span class=kind>${c.kind||''}</span><b>${c.title||''}</b><br>${c.body||''}</div>`).join('')||'<i>waiting…</i>';
  pipeline(d);}

// ROUTINES
async function routines(){const d=await j('/routines');$('rlist').innerHTML=(d.routines||[]).map(r=>`<div class=note><b>${r.name}</b> · <span class=mut>${r.cron}</span> · job=${r.job_id||''} · ${r.enabled?'on':'off'}</div>`).join('')||'<i class=mut>none</i>';}
async function addroutine(){const d=await j('/routine_add?name='+encodeURIComponent($('rname').value)+'&cron='+encodeURIComponent($('rcron').value)+'&prompt='+encodeURIComponent($('rprompt').value));routines();}
</script>
"""


async def _snapshot(native: str) -> dict:
    h = await asyncio.wait_for(harvest(_SLIM, native, seconds=3.0), timeout=8.0)
    return {"counts": h.counts(),
            "notes": [e.get("note", {}) for e in h.of("note")][-60:],
            "cards": [e.get("card", {}) for e in h.of("card")][-40:]}


async def _relay() -> dict:
    """The transcript relay's P18 health (numeric→native resolve + segment ingest) from the control plane."""
    async with httpx.AsyncClient(timeout=6.0) as c:
        r = await c.get(f"{_SLIM.base}/api/meeting/relay-health", headers=_SLIM._headers)
        r.raise_for_status()
        return r.json()


def _run(coro):
    try:
        return asyncio.run(coro)
    except Exception as e:  # surface to the page, never 500
        return {"error": str(e)}


class _H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode())

    def _json(self, data):
        self._send(200, json.dumps(data), "application/json")

    def do_POST(self):
        u = urlparse(self.path)
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0) or 0)
        data = json.loads(body or b"{}")
        if u.path == "/chat":
            res = _run(cb.chat(_SLIM, data.get("msg", ""), session=data.get("session"),
                               files=data.get("files")))
            self._json({"reply": res} if isinstance(res, str) else res)
        else:
            self._send(404, "not found", "text/plain")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        one = lambda k: (q.get(k) or [""])[0]
        if u.path == "/":
            self._send(200, _PAGE, "text/html; charset=utf-8")
        elif u.path == "/whoami":
            self._json(_run(cb.whoami(_SLIM)))
        elif u.path == "/init":
            self._json(_run(cb.init_workspace(_SLIM)))
        elif u.path == "/browse":
            r = _run(cb.browse_workspace(_SLIM))
            self._json({"entities": [p for p in r if "kg/entities/" in str(p)]} if isinstance(r, list) else r)
        elif u.path == "/readfile":
            r = _run(cb.read_workspace_file(_SLIM, one("path")))
            self._json({"content": r} if isinstance(r, str) else (r or {"content": ""}))
        elif u.path == "/snapshot":
            self._json(_run(_snapshot(one("native"))))
        elif u.path == "/relay":
            self._json(_run(_relay()))
        elif u.path == "/sendbot":
            r = _run(cb.agent_on_meeting(_SLIM, one("native"), meet_url=one("url")))
            self._json({"ok": True, "result": r} if not (isinstance(r, dict) and r.get("error")) else {"ok": False, **r})
        elif u.path == "/routines":
            r = _run(cb.list_routines(_SLIM))
            self._json({"routines": r} if isinstance(r, list) else r)
        elif u.path == "/routine_add":
            self._json(_run(cb.schedule_routine(_SLIM, one("name"), cron=one("cron"), prompt=one("prompt"))))
        else:
            self._send(404, "not found", "text/plain")

    def log_message(self, *a):
        pass


def main(host: str = "127.0.0.1", port: int = 8800) -> None:
    print(f"· vexa control panel on http://{host}:{port}  (gateway {gateway_url()})")
    ThreadingHTTPServer((host, port), _H).serve_forever()


if __name__ == "__main__":
    main()
