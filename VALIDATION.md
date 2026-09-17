# Validation — run a pull request before it merges

**A change merges here when a human who did not write it has run it and said what happened.** Not
"looks good". Not "CI is green" — the machine already said that. Someone ran it.

This page is the playbook. It is written so that you, or an agent working for you, can follow it end
to end without asking anyone anything.

**One validation makes you a validator** ([`GOVERNANCE.md`](GOVERNANCE.md) § *Roles*). It is the
lowest rung above contributor and the only one you can reach without writing a line of code.

---

## 1 · Pick something

The board is sorted by age, oldest first. Look for `needs-human-test`, or `good-first-review` if it
is your first time. Or take a pull request whose value claim is something you actually care about —
those are the best validations, because you will notice if it does not work.

**You may not validate your own pull request.**

---

## 2 · Read the value claim, not the diff

Every pull request states what job it does, for whom, and how the author knows. **That is what you
are testing.** The diff is the maintainer's problem; the claim is yours.

If a pull request has no value claim, say so in a comment and stop. You cannot validate a claim that
was never made, and asking for one is itself useful work.

---

## 3 · Run it

### Vexa Lite — one container, one command

```bash
git clone https://github.com/Vexa-ai/vexa && cd vexa
echo "IMAGE_TAG=pr-1676" >> .env      # the PR number
make lite
```

`make lite` provisions PostgreSQL and MinIO sidecars, runs the all-in-one image, and verifies the
front doors. It prints the admin token and a ready-to-use API key when it finishes.

If no `pr-<N>` image exists for the pull request yet, ask in the pull request or at the weekly
meeting — a triager can publish one. Or build it yourself from the branch:

```bash
gh pr checkout 1676
make -C deploy/lite build && make lite
```

No hosted transcription token and no GPU? `make lite LOCAL_STT=1` runs a bundled CPU Whisper server.
Slower, real transcripts.

### Compose — the full stack

```bash
gh pr checkout 1676
make dev        # builds every service from this checkout
```

**Honest note:** the per-pull-request published image is the **Lite** shape. Compose builds ten
images, so compose validation means `make dev` from the branch and a longer first build.

### Helm

```bash
gh pr checkout 1676
helm upgrade --install vexa deploy/helm/charts/vexa -f your-values.yaml
```

Only worth doing when the change touches `deploy/helm/**`, but when it does, it is the only shape
that proves anything.

---

## 4 · Exercise the claim

Do the thing the pull request says it fixes. Some of this needs a real meeting — that is fine and it
is the point; a real meeting is exactly what CI cannot have.

- **A join or lifecycle fix** — send a bot into a real call, do the thing that used to break, watch.
- **An API or payload fix** — call the endpoint, read the actual bytes.
- **A deployment fix** — run it on a machine that has never run Vexa. A cached image proves nothing
  about a first install.
- **A UI fix** — open the Terminal and look.

**Write down what you did as you go.** The specifics are the entire value of your comment.

---

## 5 · Post the result

One comment. **The first line has this exact shape:**

```
Validated on <lite|compose|helm|hosted>, <what I ran>, <result>
```

Then the evidence underneath — logs, timings, a screenshot, an API response. As much or as little as
the claim needs.

### Examples that count

```
Validated on lite, joined a 20-minute Teams call and left the bot alone for 12, bot exited
left_alone at 6m02s as claimed

deaf_for_ms=362011 max_hold_ms=360000 streams=1 frames_delivered=0
```

```
Validated on compose, POST /meetings then GET /meetings, has_capture true on the Teams row with
14 segments — the list and the detail agree now
```

```
Validated on helm, upgraded a 3-node k3s with the digest values, every pod came up and the Redis
pod runs as non-root

$ kubectl get pod -o jsonpath='{.spec.securityContext.runAsNonRoot}' → true
```

### Examples that do not count, and why

| Comment | Why not |
|---|---|
| `LGTM` | Nothing was run and nothing was claimed. |
| `Validated on lite, looks good` | No *what I ran*. |
| `CI is green` | That is the machine. The whole point is that you are not the machine. |
| The author validating their own change | Excluded by construction. |
| An agent posting as the author | **A validation names the human who ran it.** An agent's tests are not human evidence — including ours. |

### It did not work

**Say so, in the same shape.** A validation that reports a failure is worth more than one that
reports success, and it is the outcome the author most needs.

```
Validated on lite, joined a Zoom call and sent a chat message with no address, the send was
accepted silently instead of being refused — the claim does not hold on 6847ca4
```

---

## 6 · What happens next

A triager applies `validated`, the merge card grows a **Human test** row naming you, and a
maintainer reads the card. Your handle goes in the release notes next to the author's.

**And the trade, which is the only currency this queue has:**

> **Your pull request gets its human test when you have tested someone else's.**

A norm, not a check. Nobody's first contribution is held hostage to it. But the board shows who is
owed one, and it is read out every Wednesday.

---

## Questions

The weekly community meeting — every Wednesday, 14:00 UTC, 45 minutes, open to anyone. Link and
calendar in [`CONTRIBUTING.md`](CONTRIBUTING.md). Or [Discord](https://discord.gg/Ga9duGkVz9).

Rules and roles: [`GOVERNANCE.md`](GOVERNANCE.md).
