#!/usr/bin/env python3
"""Reduce a signal tape to namer inputs — csrc edges, DOM hints, roster sightings, audio ticks.

`TrackNamer` consumes nothing else. Everything the tape carries beyond these four streams — and
in bytes that is almost all of it, since a tape is mostly base64 PCM — is dropped here, which is
what makes replaying a whole corpus cheap and what keeps meeting audio out of the eval path.

Two sources, one output shape:

  LOCAL   a directory holding `*csrc.jsonl`, `*observations.jsonl`, `*captured-signal.jsonl`
          (optionally prefixed by session uid). Dogfooding harvests look like this.

  BUCKET  a production tape in object storage. Those are read INSIDE the cluster, by the reducer
          printed by `--emit-remote-reducer`: it streams the object, keeps hints and per-frame
          timestamps, and emits a gzip+base64 blob of the reduction. Audio is never written to
          disk and never leaves the cluster; only the derived namer inputs come out.

              kubectl -n <ns> exec -i <pod> -- python3 - <meeting> \
                < <(python3 normalize.py --emit-remote-reducer) > raw/<meeting>.b64

Output per tape: csrc.jsonl · hints.jsonl · roster.jsonl · ticks.jsonl · meta.json, plus
`transcript.SCRATCH.jsonl` when the tape has a transcript. The SCRATCH name is a warning that it
carries meeting content: it is for establishing truth locally and is never committed.
"""
import base64
import glob
import gzip
import json
import os
import sys

REMOTE_REDUCER = r'''
"""Runs inside the cluster. Streams one signal tape from object storage and emits ONLY namer
inputs (+ the transcript rows truth-building needs). PCM is parsed and discarded in flight."""
import boto3, os, sys, json, gzip, base64

mid = sys.argv[1]
bucket = os.environ.get('SIGNAL_BUCKET', 'vexa-recordings')
prefix = os.environ.get('SIGNAL_PREFIX', 'signal/23')
s3 = boto3.client('s3', endpoint_url=os.environ['S3_ENDPOINT'],
                  aws_access_key_id=os.environ['S3_ACCESS_KEY'],
                  aws_secret_access_key=os.environ['S3_SECRET_KEY'])
keys = []
for pg in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=f'{prefix}/{mid}/'):
    keys += [o['Key'] for o in pg.get('Contents', [])]
if not keys:
    print(json.dumps({'meeting': mid, 'error': 'no objects'})); sys.exit(0)
sess = keys[0].split('/')[3]
base = f'{prefix}/{mid}/{sess}/'

def whole(name):
    k = base + name
    return s3.get_object(Bucket=bucket, Key=k)['Body'].read().decode('utf-8', 'replace') if k in keys else None

out = {'meeting': mid, 'session': sess, 'csrc': whole('csrc.jsonl'),
       'observations': whole('observations.jsonl'), 'transcript': whole('transcript.jsonl')}
hints, audio, header = [], [], None
k = base + 'captured-signal.jsonl'
if k in keys:
    buf = b''
    for chunk in s3.get_object(Bucket=bucket, Key=k)['Body'].iter_chunks(1 << 20):
        buf += chunk
        parts = buf.split(b'\n'); buf = parts.pop()
        for line in parts:
            if not line.strip(): continue
            try: d = json.loads(line)
            except Exception: continue
            if d.get('type') == 'captured_signal_header': header = d
            elif d.get('type') == 'hint':
                hints.append({'t': d.get('t'), 'name': d.get('name'), 'isEnd': bool(d.get('isEnd'))})
            elif 'pcm_len' in d:
                audio.append([d.get('ts'), d.get('pcm_len')])
out['header'] = header; out['hints'] = hints; out['audio'] = audio
sys.stdout.write(base64.b64encode(gzip.compress(json.dumps(out, separators=(',', ':')).encode(), 6)).decode())
'''


def write(d, name, rows):
    with open(os.path.join(d, name), 'w') as f:
        for r in rows:
            f.write(json.dumps(r, separators=(',', ':')) + '\n')


def parse_obs(lines):
    """Observations → the two roster signals the namer takes: a sighting, and a coverage count."""
    roster = []
    for l in lines:
        if not l.strip():
            continue
        try:
            d = json.loads(l)
        except Exception:
            continue
        if d.get('type') != 'observation':
            continue
        ob, t = d.get('observation') or {}, d.get('t')
        if ob.get('type') == 'roster-name':
            roster.append({'t': t, 'k': 'roster-name', 'name': ob.get('name')})
        elif ob.get('type') == 'roster-coverage':
            roster.append({'t': t, 'k': 'roster-coverage', 'named': ob.get('named'), 'participants': ob.get('participants')})
    return roster


def parse_csrc(lines):
    out, hdr = [], None
    for l in lines:
        if not l.strip():
            continue
        try:
            d = json.loads(l)
        except Exception:
            continue
        if d.get('type') == 'sidecar_header':
            hdr = d
        elif d.get('type') == 'csrc':
            out.append({'t': d['t'], 'csrc': d['csrc'], 'active': bool(d['active'])})
    return out, hdr


def do_local(tid, src, out_root, source='local-harvest'):
    def find(part):
        c = glob.glob(os.path.join(src, f'*{part}.jsonl'))
        return c[0] if c else None
    fc, fo, fs = find('csrc'), find('observations'), find('captured-signal')
    if not fc or not fo or not fs:
        return {'id': tid, 'excluded': 'missing sidecars',
                'have': {'csrc': bool(fc), 'observations': bool(fo), 'captured-signal': bool(fs)}}
    csrc, hdr = parse_csrc(open(fc, errors='replace').read().split('\n'))
    roster = parse_obs(open(fo, errors='replace').read().split('\n'))
    hints, ticks, chdr = [], [], None
    with open(fs, errors='replace') as f:
        for l in f:
            if not l.strip():
                continue
            try:
                d = json.loads(l)
            except Exception:
                continue
            t = d.get('type')
            if t == 'captured_signal_header':
                chdr = d
            elif t == 'hint':
                hints.append({'t': d.get('t'), 'name': d.get('name'), 'isEnd': bool(d.get('isEnd'))})
            elif 'pcm_len' in d:
                # 16-bit mono: bytes // 2 = samples. The namer only ever divides this by the sample
                # rate to advance its clock, so the PCM itself is read and thrown away.
                ticks.append({'ts': d.get('ts'), 'samples': int(d['pcm_len']) // 2})
    ft = glob.glob(os.path.join(src, '*transcript.jsonl'))
    return emit(tid, csrc, roster, hints, ticks, hdr, chdr,
                open(ft[0], errors='replace').read() if ft else None, source, out_root)


def do_blob(tid, path, out_root):
    d = json.loads(gzip.decompress(base64.b64decode(open(path).read())))
    if d.get('error'):
        return {'id': tid, 'excluded': d['error']}
    csrc, hdr = parse_csrc((d.get('csrc') or '').split('\n'))
    roster = parse_obs((d.get('observations') or '').split('\n'))
    ticks = [{'ts': a[0], 'samples': int(a[1]) // 2} for a in d.get('audio', []) if a[1]]
    hints = [{'t': h['t'], 'name': h.get('name'), 'isEnd': bool(h.get('isEnd'))} for h in d.get('hints', [])]
    return emit(tid, csrc, roster, hints, ticks, hdr, d.get('header'),
                d.get('transcript'), 'prod-signal-tape', out_root)


def emit(tid, csrc, roster, hints, ticks, hdr, chdr, tx, source, out_root):
    d = os.path.join(out_root, tid)
    os.makedirs(d, exist_ok=True)
    write(d, 'csrc.jsonl', csrc)
    write(d, 'hints.jsonl', hints)
    write(d, 'roster.jsonl', roster)
    write(d, 'ticks.jsonl', ticks)
    ts = [c['t'] for c in csrc] + [h['t'] for h in hints] + [t['ts'] for t in ticks]
    meta = {
        'id': tid,
        'source': source,
        'platform': (hdr or chdr or {}).get('platform'),
        'session': (hdr or {}).get('session_uid') or (chdr or {}).get('trace_id'),
        'image_version': (hdr or chdr or {}).get('image_version'),
        'started_at': (hdr or chdr or {}).get('started_at'),
        'counts': {'csrc': len(csrc), 'hints': len(hints), 'roster': len(roster), 'ticks': len(ticks)},
        'tracks': sorted({str(c['csrc']) for c in csrc}),
        'roster_names': sorted({r['name'] for r in roster if r['k'] == 'roster-name' and r.get('name')}),
        'hint_names': sorted({h['name'] for h in hints if h.get('name')}),
        'duration_s': round((max(ts) - min(ts)) / 1000.0, 1) if ts else None,
    }
    json.dump(meta, open(os.path.join(d, 'meta.json'), 'w'), indent=1, ensure_ascii=False)
    if tx:
        open(os.path.join(d, 'transcript.SCRATCH.jsonl'), 'w').write(tx)
    return meta


if __name__ == '__main__':
    if '--emit-remote-reducer' in sys.argv:
        sys.stdout.write(REMOTE_REDUCER)
        sys.exit(0)
    out_root, spec_path = sys.argv[1], sys.argv[2]
    os.makedirs(out_root, exist_ok=True)
    # A spec entry is a path, or {"path", "source"} when a prod tape is being read from a local
    # copy — the provenance of a tape is a property of where it was RECORDED, not of which disk it
    # is sitting on now, and the scorecard's corpus table would misreport it otherwise.
    for tid, spec in json.load(open(spec_path)).items():
        src = spec['path'] if isinstance(spec, dict) else spec
        try:
            r = (do_blob(tid, src, out_root) if src.endswith('.b64')
                 else do_local(tid, src, out_root,
                               (spec.get('source') if isinstance(spec, dict) else None) or 'local-harvest'))
        except Exception as e:
            r = {'id': tid, 'excluded': 'reduce failed: %s' % e}
        print(json.dumps({k: v for k, v in r.items()
                          if k in ('id', 'source', 'platform', 'counts', 'tracks', 'duration_s', 'excluded')},
                         ensure_ascii=False))
