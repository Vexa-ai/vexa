#!/usr/bin/env python3
"""Join the two replay arms against ground truth and emit the corpus scorecard.

Per (tape, track) the verdict is one of:

  correct   — the published name is the truthed human
  wrong     — a human name was published and it is the WRONG human. This is the only failure that
              actually misleads a reader, so it is counted separately from `unnamed` and never
              summed with it.
  unnamed   — the track published as Speaker A/B/C. Honest, not correct: the reader learns nothing
              but is not told a falsehood.
  excluded  — no ground truth was established for this track (see the tape's `untruthed`). It is
              still replayed, and a behavioural DIFFERENCE between the arms is still reported,
              because a change we cannot score is a change we still have to look at.

A REGRESSION is baseline `correct` -> fix `wrong` or `unnamed`. That is a stop condition, printed
first and loudly; the scorecard is not a summary that averages it away.

Output is pseudonymized via the operator-side name maps; nothing here carries a real name.
"""
import argparse
import json
import os
import re

SPEAKER = re.compile(r'^Speaker [A-Z]$')


def verdict(published, truth):
    if truth is None:
        return 'excluded'
    if published is None or SPEAKER.match(published):
        return 'unnamed'
    return 'correct' if published == truth else 'wrong'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--replay', required=True)
    ap.add_argument('--truth', required=True)
    ap.add_argument('--map', required=True, help='operator-side real-name maps (never committed)')
    ap.add_argument('--out-json', required=True)
    ap.add_argument('--out-md', required=True)
    ap.add_argument('--notes', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'notes.json'))
    a = ap.parse_args()
    notes = json.load(open(a.notes)) if os.path.exists(a.notes) else {}

    rows, tapes, regressions, fixes, deltas = [], [], [], [], []
    for f in sorted(os.listdir(os.path.join(a.replay, 'fix'))):
        if not f.endswith('.json'):
            continue
        tid = f[:-5]
        fix = json.load(open(os.path.join(a.replay, 'fix', f)))
        base = json.load(open(os.path.join(a.replay, 'baseline', f)))
        truth = json.load(open(os.path.join(a.truth, f)))
        pmap = dict(json.load(open(os.path.join(a.map, f))))
        ctlp = os.path.join(a.replay, 'control', f)
        ctl = json.load(open(ctlp)) if os.path.exists(ctlp) else None

        def px(n):
            if n is None or SPEAKER.match(n):
                return n
            if n not in pmap:
                pmap[n] = 'X%d' % (1 + sum(1 for v in pmap.values() if v.startswith('X')))
            return pmap[n]

        tapes.append({
            'tape': tid, 'duration_s': truth['duration_s'], 'tracks': len(fix['tracks']),
            'source': truth.get('source'), 'image_version': truth.get('image_version'),
            'truthed': len(truth['tracks']), 'counts': truth['counts'], 'judge': truth.get('judge'),
            'roster_first_seen_s': truth['roster_first_seen_s'],
        })
        for tr in sorted(fix['tracks']):
            b, x = base['tracks'][tr], fix['tracks'][tr]
            t = (truth['tracks'].get(tr) or {}).get('name')
            bn, xn = px(b['finalName']), px(x['finalName'])
            cn = px(ctl['tracks'][tr]['finalName']) if ctl and tr in ctl['tracks'] else None
            bv, xv = verdict(bn, t), verdict(xn, t)
            row = {
                'tape': tid, 'track': tr, 'truth': t,
                'truth_purity': (truth['tracks'].get(tr) or {}).get('purity'),
                'truth_instants': (truth['tracks'].get(tr) or {}).get('instants'),
                'untruthed_reason': truth['untruthed'].get(tr),
                'baseline': {'name': bn, 'verdict': bv, 'first_s': b['firstNameAtS'],
                             'first_was': px(b['firstNameWas']), 'final_s': b['finalNameAtS'],
                             'retractions': [{'from': px(r['from']), 'to': px(r['to']), 'atS': r['atS']} for r in b['retractions']]},
                'fix': {'name': xn, 'verdict': xv, 'first_s': x['firstNameAtS'],
                        'first_was': px(x['firstNameWas']), 'final_s': x['finalNameAtS'],
                        'retractions': [{'from': px(r['from']), 'to': px(r['to']), 'atS': r['atS']} for r in x['retractions']]},
                'control_name': cn,
                'changed': bn != xn,
                'note': notes.get('%s/%s' % (tid, tr)),
            }
            rows.append(row)
            if row['changed']:
                deltas.append(row)
            if bv == 'correct' and xv in ('wrong', 'unnamed'):
                regressions.append(row)
            if bv in ('wrong', 'unnamed') and xv == 'correct':
                fixes.append(row)

    scored = [r for r in rows if r['truth'] is not None]
    def tally(arm):
        return {v: sum(1 for r in scored if r[arm]['verdict'] == v) for v in ('correct', 'wrong', 'unnamed')}
    summary = {
        'tapes': len(tapes), 'tracks': len(rows), 'scored_tracks': len(scored),
        'excluded_tracks': len(rows) - len(scored),
        'baseline': tally('baseline'), 'fix': tally('fix'),
        'regressions': len(regressions), 'repairs': len(fixes), 'changed_tracks': len(deltas),
    }

    # Latency is compared only where BOTH arms published a human name — a track that one arm never
    # names has no first-name time, and treating that as "infinitely slow" would let a correctness
    # change masquerade as a latency change.
    lat = [(r['baseline']['first_s'], r['fix']['first_s']) for r in rows
           if r['baseline']['first_s'] is not None and r['fix']['first_s'] is not None]
    summary['latency_pairs'] = len(lat)
    summary['latency_first_name_identical'] = sum(1 for b, x in lat if b == x)
    summary['latency_first_name_delta_s'] = sorted({round(x - b, 1) for b, x in lat})
    # Publishing a revisable name buys correctness with repaints. That is a real cost and it is
    # counted here rather than left for a reader to discover in production.
    churn = [r for r in rows if len(r['fix']['retractions']) != len(r['baseline']['retractions'])]
    summary['retractions_baseline'] = sum(len(r['baseline']['retractions']) for r in rows)
    summary['retractions_fix'] = sum(len(r['fix']['retractions']) for r in rows)
    summary['tracks_with_new_churn'] = len(churn)
    json.dump({'summary': summary, 'tapes': tapes, 'rows': rows}, open(a.out_json, 'w'), indent=1)

    L = []
    W = L.append
    W('# Namer corpus scorecard — `fix/namer-roster-settle` vs its parent commit\n')
    W('Every Teams tape reachable to this workspace, replayed through both namers with identical')
    W('inputs. Names are pseudonymized per tape (`P1`, `P2`, …); the map is operator-side.\n')
    if regressions:
        W('## STOP — regressions\n')
        W('| tape | track | truth | baseline | fix |')
        W('|---|---|---|---|---|')
        for r in regressions:
            W('| %s | csrc:%s | %s | **%s** (correct) | **%s** (%s) |' %
              (r['tape'], r['track'], r['truth'], r['baseline']['name'], r['fix']['name'], r['fix']['verdict']))
        W('')
    else:
        W('**No regressions.** No track that the pre-fix namer named correctly is named wrong,')
        W('or left unnamed, by the fix.\n')
    W('## Totals (scored tracks only)\n')
    W('| | correct | wrong | unnamed |')
    W('|---|---|---|---|')
    for arm in ('baseline', 'fix'):
        t = summary[arm]
        W('| %s | %d | %d | %d |' % (arm, t['correct'], t['wrong'], t['unnamed']))
    W('')
    judged = [t for t in tapes if t.get('judge')]
    if judged:
        W('Ground truth on %s is corroborated by all three independent lines — mechanical'
          % ', '.join(t['tape'] for t in judged))
        W('hint × csrc correlation, the settled-window control replay, and the label-blind')
        W('vocative / self-identification judge of Vexa-ai/vexa#1224 (flagged precision 1.0 in')
        W('Vexa-ai/vexa#1225). Every other tape rests on the first two.\n')
    W('Corpus: **%d tapes**, %d tracks, of which **%d carry established ground truth** and %d are'
      % (summary['tapes'], summary['tracks'], summary['scored_tracks'], summary['excluded_tracks']))
    W('excluded. Tracks whose name changed between the arms: **%d**.\n' % summary['changed_tracks'])
    W('## Corpus\n')
    W('| tape | source | image | duration | tracks | truthed | csrc edges | hints | roster |')
    W('|---|---|---|---|---|---|---|---|---|')
    for t in sorted(tapes, key=lambda t: t['tape']):
        c = t['counts']
        W('| %s | %s | `%s` | %s | %d | %d | %d | %d | %d |' % (
            t['tape'], t.get('source') or '—', t.get('image_version') or '—',
            '%d:%02d' % (int(t['duration_s']) // 60, int(t['duration_s']) % 60),
            t['tracks'], t['truthed'], c['csrc'], c['hints'], c['roster']))
    W('')
    W('## Every track\n')
    W('| tape | track | truth | purity | baseline | fix | control | verdict b→f |')
    W('|---|---|---|---|---|---|---|---|')
    for r in sorted(rows, key=lambda r: (r['tape'], r['track'])):
        W('| %s | csrc:%s | %s | %s | %s | %s | %s | %s → %s%s |' % (
            r['tape'], r['track'], r['truth'] or '—',
            ('%.2f' % r['truth_purity']) if r['truth_purity'] else '—',
            r['baseline']['name'] or '—', r['fix']['name'] or '—', r['control_name'] or '—',
            r['baseline']['verdict'], r['fix']['verdict'], ' **Δ**' if r['changed'] else ''))
    W('')
    W('## Exclusions — every track the oracle could not truth, and why\n')
    W('No silent drops. A track is excluded when the mechanical oracle cannot establish who owns')
    W('it, never because the result was inconvenient; both arms are still replayed on it and any')
    W('behavioural difference is still reported above.\n')
    W('| tape | track | reason |')
    W('|---|---|---|')
    for r in sorted(rows, key=lambda r: (r['tape'], r['track'])):
        if r['truth'] is None:
            W('| %s | csrc:%s | %s |' % (r['tape'], r['track'], r['untruthed_reason'] or 'no ground truth'))
    W('')
    if any(r['note'] for r in rows):
        W('### Row notes\n')
        for r in sorted(rows, key=lambda r: (r['tape'], r['track'])):
            if r['note']:
                W('- **%s csrc:%s** — %s' % (r['tape'], r['track'], r['note']))
        W('')
    W('## Label churn — what publishing a revisable name costs\n')
    W('A retraction is a published human name that a later evaluation replaced. The pre-fix namer')
    W('cannot produce one: an evidence name was permanent. The fix trades that permanence for')
    W('correctness, and the trade is not free.\n')
    W('| | baseline | fix |')
    W('|---|---|---|')
    W('| retraction events, whole corpus | %d | %d |' % (summary['retractions_baseline'], summary['retractions_fix']))
    W('| tracks whose label stream changed | — | %d |' % summary['tracks_with_new_churn'])
    W('')
    if churn:
        W('| tape | track | truth | fix label stream | final |')
        W('|---|---|---|---|---|')
        for r in sorted(churn, key=lambda r: (r['tape'], r['track'])):
            fx = json.load(open(os.path.join(a.replay, 'fix', r['tape'] + '.json')))['tracks'][r['track']]
            pmap = dict(json.load(open(os.path.join(a.map, r['tape'] + '.json'))))
            stream = ' → '.join((pmap.get(e['name'], e['name'])) + ('@%gs' % e['atS']) for e in fx['events'])
            W('| %s | csrc:%s | %s | %s | %s (%s) |' % (r['tape'], r['track'], r['truth'] or '—', stream,
                                                        r['fix']['name'], r['fix']['verdict']))
        W('')
    W('## Naming latency\n')
    W('| | |')
    W('|---|---|')
    W('| tracks where both arms published a name | %d |' % summary['latency_pairs'])
    W('| of those, identical time-to-first-name | %d |' % summary['latency_first_name_identical'])
    W('| distinct first-name deltas (fix − baseline, s) | %s |' %
      ', '.join('%+g' % d for d in summary['latency_first_name_delta_s']))
    W('')
    open(a.out_md, 'w').write('\n'.join(L) + '\n')
    print(json.dumps(summary, indent=1))
    for r in regressions:
        print('REGRESSION', r['tape'], r['track'], r['truth'], r['baseline']['name'], '->', r['fix']['name'])


if __name__ == '__main__':
    main()
