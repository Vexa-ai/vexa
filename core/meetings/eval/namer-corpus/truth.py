#!/usr/bin/env python3
"""Establish per-track ground truth for one tape, mechanically, and say how confident it is.

A tape earns a place in the scorecard only if its truth is established INDEPENDENTLY of the namer
under test. Three lines are available; each is computed here or recorded here, and a tape's
confidence is a function of how many agree.

  1. HINT x CSRC EXCLUSIVE CORRELATION (this file, primary).
     Teams' DOM speaking-indicator ("hint") says WHO is speaking; the RTP csrc edge says WHICH
     transport track is carrying audio. Neither alone identifies a track, but a hint that lands
     while exactly ONE track is active attributes that instant unambiguously — the ambiguity is
     precisely the overlap, so overlapping instants are DISCARDED rather than modelled. Over a
     whole meeting the surviving instants form a per-track name histogram. Truth requires both:
       * purity   — the leading name holds >= PURITY of that track's unambiguous instants;
       * bijection — every truthed track's leading name is different, and each name's instants
                     are concentrated on one track (>= OWNER share of that name's total).
     This is NOT the namer's own computation. The namer decides ONLINE, from a prefix of the
     tape, under a settle delay and an exclusivity rule that fires on first-past-the-post. This
     is a whole-meeting aggregate with a global bijection constraint — it can only be computed
     after the meeting ends, which is exactly why it can serve as an oracle for something that
     cannot wait.

  2. SETTLED-WINDOW CONTROL REPLAY (replay.ts, CUT_MS).
     Replay with every event before the roster finished discovering the room dropped. The
     premature-acceptance hazard cannot fire in that window because the window is gone, so the
     names that come out are what the evidence says with the timing hazard removed. Recorded per
     tape by run.sh and joined here.

  3. LINGUISTIC SELF-INCRIMINATION (#1224 judge: vocative + self-identification).
     Where a judged verdict exists for the tape it is carried in `judge` and cited. Vexa-ai/vexa
     PR #1225 measured this judge at flagged-precision 1.0 on the two witnessed meetings.

Output is PSEUDONYMIZED: real names are replaced by stable per-tape P1..PN. The map stays with
the operator (--map-out), never in the repository.
"""
import argparse
import json
import os
from collections import defaultdict

# A track's leading name must hold this share of the track's unambiguous instants.
PURITY = 0.80
# ...and that name's instants must be this concentrated on that one track.
OWNER = 0.70
# Below this many unambiguous instants a histogram is noise, not evidence.
MIN_INSTANTS = 5


def load(d, name):
    p = os.path.join(d, name)
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p) if l.strip()]


def active_intervals(csrc):
    """csrc edges -> {track: [(start, end)]}. An unterminated span runs to the last edge."""
    open_at, out = {}, defaultdict(list)
    last = max((c['t'] for c in csrc), default=0)
    for c in sorted(csrc, key=lambda c: c['t']):
        k = str(c['csrc'])
        if c['active']:
            open_at.setdefault(k, c['t'])
        elif k in open_at:
            out[k].append((open_at.pop(k), c['t']))
    for k, s in open_at.items():
        out[k].append((s, last))
    return out


def active_at(iv, t):
    return [k for k, spans in iv.items() if any(a <= t <= b for a, b in spans)]


def correlate(csrc, hints):
    """Per-track name histogram from hints that land while exactly one track is active."""
    iv = active_intervals(csrc)
    hist = defaultdict(lambda: defaultdict(int))
    ambiguous = 0
    for h in hints:
        if h.get('isEnd') or not h.get('name'):
            continue
        act = active_at(iv, h['t'])
        if len(act) == 1:
            hist[act[0]][h['name']] += 1
        elif len(act) > 1:
            ambiguous += 1
    return iv, hist, ambiguous


def build(tape_dir, judge=None):
    meta = json.load(open(os.path.join(tape_dir, 'meta.json')))
    csrc, hints = load(tape_dir, 'csrc.jsonl'), load(tape_dir, 'hints.jsonl')
    roster = load(tape_dir, 'roster.jsonl')
    iv, hist, ambiguous = correlate(csrc, hints)

    name_total = defaultdict(int)
    for t, names in hist.items():
        for n, c in names.items():
            name_total[n] += c

    tracks, notes = {}, {}
    for t in meta['tracks']:
        names = hist.get(t, {})
        n = sum(names.values())
        if n < MIN_INSTANTS:
            notes[t] = f'no truth: {n} unambiguous hint instants (< {MIN_INSTANTS})'
            continue
        lead, lead_n = max(names.items(), key=lambda kv: kv[1])
        purity = lead_n / n
        owner = lead_n / name_total[lead] if name_total[lead] else 0
        if purity < PURITY:
            notes[t] = f'no truth: leading name purity {purity:.2f} < {PURITY}'
            continue
        if owner < OWNER:
            # {} is filled with the PSEUDONYM below — a reason string is committed output and may
            # not carry a real participant name.
            notes[t] = f'no truth: "{{{lead}}}" is {owner:.2f} concentrated on this track (< {OWNER})'
            continue
        tracks[t] = {'name': lead, 'instants': n, 'purity': round(purity, 4), 'owner_share': round(owner, 4)}

    # Bijection: two tracks may not both be the same human.
    seen = defaultdict(list)
    for t, v in tracks.items():
        seen[v['name']].append(t)
    for n, ts in seen.items():
        if len(ts) > 1:
            for t in ts:
                notes[t] = f'no truth: "{{{n}}}" leads {len(ts)} tracks — bijection fails'
                tracks.pop(t, None)

    # Roster timeline: when each name was FIRST sighted, relative to the tape's own start. This is
    # the quantity the fix is about — a name accepted before the roster has seen the second human.
    t0 = min([c['t'] for c in csrc] + [h['t'] for h in hints] + [r['t'] for r in roster], default=0)
    first_seen, coverage = {}, []
    for r in roster:
        if r['k'] == 'roster-name' and r.get('name') and r['name'] not in first_seen:
            first_seen[r['name']] = round((r['t'] - t0) / 1000, 1)
        elif r['k'] == 'roster-coverage':
            coverage.append([round((r['t'] - t0) / 1000, 1), r.get('named'), r.get('participants')])

    reals = sorted({v['name'] for v in tracks.values()} | set(first_seen)
                   | set(meta.get('hint_names') or []) | set(meta.get('roster_names') or []))
    pseudo = {r: f'P{i + 1}' for i, r in enumerate(reals)}
    px = lambda s: pseudo.get(s, s)

    # Reason strings are committed. Substitute the pseudonym for the braced real name; a name the
    # map somehow missed becomes "?" rather than leaking.
    def redact(s):
        out, i = '', 0
        while True:
            a = s.find('{', i)
            if a < 0:
                return out + s[i:]
            b = s.find('}', a)
            out += s[i:a] + pseudo.get(s[a + 1:b], '?')
            i = b + 1
    notes = {t: redact(v) for t, v in notes.items()}

    return {
        'tape': meta['id'],
        'platform': meta['platform'],
        'source': meta.get('source'),
        'image_version': meta.get('image_version'),
        'duration_s': meta['duration_s'],
        'lane': 'teams-csrc',
        'method': 'hint x csrc exclusive correlation (whole-meeting, bijective)',
        'thresholds': {'purity': PURITY, 'owner_share': OWNER, 'min_instants': MIN_INSTANTS},
        'tracks': {t: {**v, 'name': px(v['name'])} for t, v in tracks.items()},
        'untruthed': notes,
        'ambiguous_hint_instants': ambiguous,
        'roster_first_seen_s': {px(k): v for k, v in first_seen.items()},
        'roster_coverage': coverage,
        'judge': judge,
        'counts': meta['counts'],
    }, pseudo


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('tape_dir')
    ap.add_argument('--out', required=True)
    ap.add_argument('--map-out', required=True, help='real-name map — operator-side, never committed')
    ap.add_argument('--judge', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'judge.json'))
    a = ap.parse_args()
    judged = json.load(open(a.judge)) if os.path.exists(a.judge) else {}
    tid = json.load(open(os.path.join(a.tape_dir, 'meta.json')))['id']
    truth, pseudo = build(a.tape_dir, judge=judged.get(tid))
    json.dump(truth, open(a.out, 'w'), indent=1, ensure_ascii=False)
    json.dump(pseudo, open(a.map_out, 'w'), indent=1, ensure_ascii=False)
    print(json.dumps({'tape': truth['tape'], 'tracks': truth['tracks'], 'untruthed': truth['untruthed']}, ensure_ascii=False))
