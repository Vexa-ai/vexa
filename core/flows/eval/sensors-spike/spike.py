"""ADR-0050 spike: one shared calendar sensor per connection -> typed event -> keyed one-shot.

Proves the SHAPE, not the product. Three processes in one:
  * a fake calendar provider (separate process, HTTP) with Google-style incremental sync:
    `GET /cal/<conn>/events?syncToken=T` returns only what changed since T plus nextSyncToken;
    an unknown token is 410 (full resync), exactly the contract the real sensor must honour;
  * a sensor pool: a timer heap of N sensors (one per connection) served by K shared threads —
    no process or container per sensor;
  * a trigger matcher: calendar.event.* -> filter (video link, not declined) -> UPSERT a keyed
    schedule `cal:<conn>:<event_id>` at start - lead; cancel on cancelled/declined. A firer claims
    due schedules FOR UPDATE SKIP LOCKED and writes one idempotent reaction per (key, fire_at).

Usage:
  python spike.py scenario --dsn ...          # correctness: create -> move -> cancel -> fire once
  python spike.py load --dsn ... --sensors 10000 --interval 15 --seconds 60 --threads 16
"""
from __future__ import annotations

import argparse
import heapq
import json
import multiprocessing as mp
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pg8000.native as pg

PORT = int(os.environ.get("SPIKE_PORT", "18777"))

# ----------------------------------------------------------------------------------------------
# Fake provider (stand-in for Calendar events.list with syncToken; Graph delta is the same shape)
# ----------------------------------------------------------------------------------------------

def provider_main(port: int) -> None:
    lock = threading.Lock()
    cals: dict[str, dict] = {}          # conn -> {"seq": int, "events": {id: (seq, body)}}
    calls = {"list": 0, "full": 0, "410": 0}

    def cal(conn):
        return cals.setdefault(conn, {"seq": 1, "events": {}})

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == "/stats":
                return self._send(200, calls)
            parts = u.path.strip("/").split("/")      # cal/<conn>/events
            q = urllib.parse.parse_qs(u.query)
            with lock:
                calls["list"] += 1
                c = cal(parts[1])
                tok = (q.get("syncToken") or [None])[0]
                if tok is None:
                    calls["full"] += 1
                    since = 0
                else:
                    try:
                        since = int(tok)
                    except ValueError:
                        calls["410"] += 1
                        return self._send(410, {"error": "fullSyncRequired"})
                items = [b for (s, b) in c["events"].values() if s > since]
                if tok is None:
                    items = [b for b in items if b.get("status") != "cancelled"]
                return self._send(200, {"items": items, "nextSyncToken": str(c["seq"])})

        def do_POST(self):                           # test driver: upsert/cancel an event
            parts = self.path.strip("/").split("/")
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            with lock:
                c = cal(parts[1])
                c["seq"] += 1
                c["events"][body["id"]] = (c["seq"], body)
            return self._send(200, {"ok": True})

    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


def provider_put(conn: str, ev: dict) -> None:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/cal/{conn}/events",
                                 data=json.dumps(ev).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


# ----------------------------------------------------------------------------------------------
# Store
# ----------------------------------------------------------------------------------------------

DDL = [
    "DROP TABLE IF EXISTS sensor_cursor, event_log, keyed_schedule, reaction_spike",
    """CREATE TABLE sensor_cursor (sensor_id TEXT PRIMARY KEY, cursor TEXT, updated_at DOUBLE PRECISION)""",
    """CREATE TABLE event_log (dedupe_key TEXT PRIMARY KEY, event_type TEXT NOT NULL,
         uid TEXT NOT NULL, payload TEXT NOT NULL, lineage TEXT NOT NULL, created_at DOUBLE PRECISION)""",
    """CREATE TABLE keyed_schedule (scope TEXT NOT NULL, key TEXT NOT NULL, trigger_id TEXT NOT NULL,
         fire_at DOUBLE PRECISION NOT NULL, status TEXT NOT NULL, version INT NOT NULL,
         payload TEXT NOT NULL, updated_at DOUBLE PRECISION, PRIMARY KEY (scope, key, trigger_id))""",
    "CREATE INDEX keyed_due ON keyed_schedule (fire_at) WHERE status = 'pending'",
    """CREATE TABLE reaction_spike (source_event_id TEXT PRIMARY KEY, step TEXT, payload TEXT,
         created_at DOUBLE PRECISION)""",
]


def connect(dsn: str) -> pg.Connection:
    u = urllib.parse.urlparse(dsn)
    return pg.Connection(user=u.username, password=u.password, host=u.hostname,
                         port=u.port or 5432, database=u.path.lstrip("/"))


# ----------------------------------------------------------------------------------------------
# Sensor (no LLM): incremental read -> typed events
# ----------------------------------------------------------------------------------------------

class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.ticks = self.changed = self.events = self.upserts = self.cancels = 0
        self.db_stmts = 0
        self.cpu_s = 0.0
        self.wall_s = 0.0
        self.lag = []

    def add(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, getattr(self, k) + v)


def has_video(ev):
    return bool(ev.get("hangoutLink") or "meet.google.com" in (ev.get("location") or "")
                or "teams.microsoft.com" in (ev.get("description") or ""))


def declined_by_owner(ev):
    return any(a.get("self") and a.get("responseStatus") == "declined" for a in ev.get("attendees", []))


class Sensor:
    """One sensor per connection. Cursor kept in memory, checkpointed only when it moves —
    a no-change tick touches the provider once and the database zero times."""

    def __init__(self, conn: str, uid: str):
        self.conn, self.uid, self.cursor = conn, uid, None

    def tick(self, db: pg.Connection, stats: Stats, lead_s: float) -> None:
        url = f"http://127.0.0.1:{PORT}/cal/{self.conn}/events"
        if self.cursor:
            url += "?syncToken=" + urllib.parse.quote(self.cursor)
        try:
            data = json.loads(urllib.request.urlopen(url, timeout=5).read())
        except urllib.error.HTTPError as e:
            if e.code == 410:
                self.cursor = None              # full resync; dedupe absorbs the replay
                return
            raise
        items, nxt = data["items"], data["nextSyncToken"]
        if not items and nxt == self.cursor:
            return
        first = self.cursor is None
        n_ev = n_up = n_ca = stmts = 0
        db.run("BEGIN")                          # one commit (one fsync) per changed tick
        for ev in items:
            etype = ("calendar.event.cancelled" if ev.get("status") == "cancelled"
                     else "calendar.event.created" if first else "calendar.event.updated")
            dk = f"gcal:{self.conn}:{ev['id']}:{ev.get('etag', '')}"
            rows = db.run("""INSERT INTO event_log VALUES (:k,:t,:u,:p,:l,:n)
                             ON CONFLICT DO NOTHING RETURNING 1""",
                          k=dk, t=etype, u=self.uid, p=json.dumps(ev), l="[]", n=time.time())
            stmts += 1
            if not rows:
                continue                         # replayed after a resync: already seen
            n_ev += 1
            # --- trigger matcher: subscription "bot to every video meeting, 1 min before" ---
            key = f"cal:{self.conn}:{ev['id']}"
            if etype == "calendar.event.cancelled" or declined_by_owner(ev) or not has_video(ev):
                r = db.run("""UPDATE keyed_schedule SET status='cancelled', version=version+1,
                                  updated_at=:n WHERE scope=:s AND key=:k AND trigger_id='t1'
                                  AND status='pending' RETURNING 1""",
                           s=self.uid, k=key, n=time.time())
                stmts += 1
                n_ca += 1 if r else 0
                continue
            fire_at = float(ev["start"]) - lead_s
            db.run("""INSERT INTO keyed_schedule VALUES (:s,:k,'t1',:f,'pending',1,:p,:n)
                      ON CONFLICT (scope,key,trigger_id) DO UPDATE SET fire_at=EXCLUDED.fire_at,
                        status='pending', version=keyed_schedule.version+1,
                        payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at
                      WHERE keyed_schedule.status <> 'fired' OR keyed_schedule.fire_at <> EXCLUDED.fire_at""",
                   s=self.uid, k=key, f=fire_at, p=json.dumps({"meeting_url": ev.get("hangoutLink")}),
                   n=time.time())
            stmts += 1
            n_up += 1
        db.run("""INSERT INTO sensor_cursor VALUES (:i,:c,:n) ON CONFLICT (sensor_id)
                  DO UPDATE SET cursor=EXCLUDED.cursor, updated_at=EXCLUDED.updated_at""",
               i=self.conn, c=nxt, n=time.time())
        db.run("COMMIT")
        stmts += 1
        self.cursor = nxt
        stats.add(changed=1, events=n_ev, upserts=n_up, cancels=n_ca, db_stmts=stmts)


class Pool:
    """Timer heap + K shared threads. The heap is the wheel: O(log n) per reschedule, one
    lock, jitter spreads sensors over the interval so thousands never fire in one burst."""

    def __init__(self, dsn, sensors, interval, threads, lead_s, stats):
        now = time.time()
        self.heap = [(now + random.random() * interval, i) for i in range(len(sensors))]
        heapq.heapify(self.heap)
        self.sensors, self.interval, self.lead_s, self.stats = sensors, interval, lead_s, stats
        self.cv = threading.Condition()
        self.stop = False
        self.threads = [threading.Thread(target=self.run, args=(dsn,), daemon=True)
                        for _ in range(threads)]

    def run(self, dsn):
        db = connect(dsn)
        while True:
            with self.cv:
                while not self.stop:
                    due, i = self.heap[0]
                    delay = due - time.time()
                    if delay <= 0:
                        heapq.heappop(self.heap)
                        break
                    self.cv.wait(min(delay, 0.5))
                if self.stop:
                    return
            c0, w0 = time.thread_time(), time.perf_counter()
            try:
                self.sensors[i].tick(db, self.stats, self.lead_s)
            except Exception as e:  # a failing sensor backs off; it never stops the pool
                print("tick error", self.sensors[i].conn, e)
            c1, w1 = time.thread_time(), time.perf_counter()
            self.stats.add(ticks=1, cpu_s=c1 - c0, wall_s=w1 - w0)
            with self.stats.lock:
                self.stats.lag.append(time.time() - due)
            with self.cv:
                heapq.heappush(self.heap, (due + self.interval, i))
                self.cv.notify()

    def start(self):
        for t in self.threads:
            t.start()

    def shutdown(self):
        with self.cv:
            self.stop = True
            self.cv.notify_all()


def fire_due(db: pg.Connection) -> list:
    """The firer: claim due keyed schedules, write ONE reaction per (key, version)."""
    fired = []
    rows = db.run("""SELECT scope, key, trigger_id, fire_at, version, payload FROM keyed_schedule
                     WHERE status='pending' AND fire_at <= :n ORDER BY fire_at
                     FOR UPDATE SKIP LOCKED LIMIT 100""", n=time.time())
    for scope, key, tid, fire_at, version, payload in rows:
        sid = f"sched:{scope}/{key}/{tid}:{fire_at:.0f}"
        db.run("INSERT INTO reaction_spike VALUES (:s,'request_meeting_bot',:p,:n) ON CONFLICT DO NOTHING",
               s=sid, p=payload, n=time.time())
        db.run("UPDATE keyed_schedule SET status='fired' WHERE scope=:s AND key=:k AND trigger_id=:t",
               s=scope, k=key, t=tid)
        fired.append((key, fire_at, time.time() - fire_at))
    return fired


# ----------------------------------------------------------------------------------------------

def setup(dsn):
    db = connect(dsn)
    for s in DDL:
        db.run(s)
    return db


def start_provider():
    p = mp.Process(target=provider_main, args=(PORT,), daemon=True)
    p.start()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/stats", timeout=1).read()
            return p
        except Exception:
            time.sleep(0.1)
    raise SystemExit("provider did not start")


def scenario(a):
    db = setup(a.dsn)
    start_provider()
    stats = Stats()
    sensors = [Sensor("conn-a", "u1")]
    pool = Pool(a.dsn, sensors, a.interval, 1, a.lead, stats)
    t0 = time.time()
    me = {"self": True, "responseStatus": "accepted"}
    provider_put("conn-a", {"id": "ev1", "etag": "1", "start": t0 + 20, "hangoutLink": "https://meet.google.com/abc", "attendees": [me]})
    provider_put("conn-a", {"id": "ev2", "etag": "1", "start": t0 + 20, "location": "room 4", "attendees": [me]})
    provider_put("conn-a", {"id": "ev3", "etag": "1", "start": t0 + 20, "hangoutLink": "https://meet.google.com/def",
                            "attendees": [{"self": True, "responseStatus": "declined"}]})
    provider_put("conn-a", {"id": "ev4", "etag": "1", "start": t0 + 22, "hangoutLink": "https://meet.google.com/ghi", "attendees": [me]})
    pool.start()
    log = []
    moved = cancelled = False
    while time.time() - t0 < 45:
        t = time.time() - t0
        if t > 6 and not moved:            # reschedule ev1 by +10 s
            provider_put("conn-a", {"id": "ev1", "etag": "2", "start": t0 + 30, "hangoutLink": "https://meet.google.com/abc", "attendees": [me]})
            moved = True
        if t > 8 and not cancelled:        # cancel ev4
            provider_put("conn-a", {"id": "ev4", "etag": "2", "status": "cancelled"})
            cancelled = True
        for key, fire_at, late in fire_due(db):
            log.append({"key": key, "fire_at_rel": round(fire_at - t0, 2), "late_s": round(late, 3)})
        time.sleep(0.2)
    pool.shutdown()
    sched = db.run("SELECT key, status, version, fire_at - :t FROM keyed_schedule ORDER BY key", t=t0)
    reac = db.run("SELECT source_event_id FROM reaction_spike")
    out = {"fired": log, "schedules": [[k, s, v, round(f, 1)] for k, s, v, f in sched],
           "reactions": len(reac), "events": db.run("SELECT event_type, count(*) FROM event_log GROUP BY 1")}
    print(json.dumps(out, indent=1))
    ok = (len(reac) == 1 and log and log[0]["key"] == "cal:conn-a:ev1"
          and abs(log[0]["fire_at_rel"] - (30 - a.lead)) < 0.5)
    print("SCENARIO", "PASS" if ok else "FAIL")


def load(a):
    setup(a.dsn)
    prov = start_provider()
    stats = Stats()
    sensors = [Sensor(f"conn-{i}", f"u{i}") for i in range(a.sensors)]
    now = time.time()
    # Seed: every connection has 3 meetings; then a trickle of changes during the run
    # (a.change_rate per sensor-tick), the realistic shape: almost every tick finds nothing.
    for i in range(a.sensors):
        for j in range(3):
            provider_put(f"conn-{i}", {"id": f"e{j}", "etag": "1", "start": now + 3600 + j * 600,
                                       "hangoutLink": "https://meet.google.com/x"})
    pool = Pool(a.dsn, sensors, a.interval, a.threads, a.lead, stats)
    proc_c0 = time.process_time()
    pool.start()
    t0 = time.time()
    warm_done = False
    base = None
    while not warm_done or time.time() - ts < a.seconds:
        if not warm_done and stats.changed >= a.sensors:
            # every sensor has done its initial full sync; measure steady state from here
            with stats.lock:
                base = (stats.ticks, stats.cpu_s, stats.wall_s, stats.db_stmts, stats.changed, len(stats.lag))
            pc_base = time.process_time()
            ts = time.time()
            warm_done = True
        if warm_done and random.random() < 0.5:
            i = random.randrange(a.sensors)
            provider_put(f"conn-{i}", {"id": "e0", "etag": str(time.time()), "start": now + 7200,
                                       "hangoutLink": "https://meet.google.com/x"})
        time.sleep(0.05)
    pool.shutdown()
    el = time.time() - ts
    print("warm-up (initial full sync of all sensors) took", round(ts - t0, 1), "s")
    ticks = stats.ticks - base[0]
    lag = sorted(stats.lag[base[5]:])
    res = {
        "sensors": a.sensors, "interval_s": a.interval, "threads": a.threads,
        "steady_window_s": round(el, 1), "ticks": ticks, "ticks_per_s": round(ticks / el, 1),
        "changed_ticks": stats.changed - base[4],
        "cpu_ms_per_tick_sensor_side": round((stats.cpu_s - base[1]) / ticks * 1000, 3),
        "process_cpu_ms_per_tick": round((time.process_time() - pc_base) / ticks * 1000, 3),
        "wall_ms_per_tick": round((stats.wall_s - base[2]) / ticks * 1000, 3),
        "db_stmts_per_tick": round((stats.db_stmts - base[3]) / ticks, 4),
        "sched_lag_p50_ms": round(lag[len(lag) // 2] * 1000, 1) if lag else None,
        "sched_lag_p99_ms": round(lag[int(len(lag) * .99)] * 1000, 1) if lag else None,
        "provider": json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/stats").read()),
    }
    print(json.dumps(res, indent=1))
    prov.terminate()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["scenario", "load"])
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--sensors", type=int, default=1000)
    ap.add_argument("--interval", type=float, default=15)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--lead", type=float, default=5)
    a = ap.parse_args()
    (scenario if a.mode == "scenario" else load)(a)
