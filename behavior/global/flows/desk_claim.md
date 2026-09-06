---
kind: flow
flow: desk_claim
version: 1
trigger: claim.proposed
steps: 1
generated: from the code that runs it — edits here are overwritten
---

# desk_claim

Runs when **`claim.proposed`** happens, in 1 step. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `claim.proposed` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | nothing |
| **rules it honours** | none |

## The steps, in order

### 1. `await_claim`

The QUESTION card: one proposed claim, waiting for a person to confirm or correct it.

- **domains:** without **agent** the reaction ends there, saying so

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<details>
<summary>view source — <code>await_claim</code></summary>

```python
@reg.step(needs=("agent",))
def await_claim(ctx: StepCtx):
    """The QUESTION card: one proposed claim, waiting for a person to confirm or correct it.

    Same re-read, same reason. The block's reason carries the CLAIM ITSELF and nothing else:
    it is this person's data, not our prose, and the sentence around it is
    `behavior/queue/desk_claim.human.md`'s.
    """
    uid = str(ctx.refs.get("uid") or "").strip()
    cid = str(ctx.refs.get("claim_id") or "").strip()
    if not uid or not cid:
        raise StepError("claim.proposed needs a uid and a claim_id — without both there is "
                        "nothing to look up and nothing to resolve", retryable=False)
    try:
        book = json.loads(p.ws_file(uid, CLAIM_BOOK) or "{}")
    except Exception:  # noqa: BLE001 — an unparseable book is not this reaction's failure
        book = {}
    claim = next((c for c in (book.get("claims") or []) if str(c.get("id")) == cid), None)
    if not claim or claim.get("state") != "proposed":
        return Done({"claim_id": cid, "outcome": "already_answered"})
    return Block(str(claim.get("claim") or "")[:200] or "claim proposed")
```

</details>
