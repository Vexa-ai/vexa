---
kind: flow
flow: meeting_prep
version: 1
trigger: meeting.upcoming
steps: 1
generated: from the code that runs it — edits here are overwritten
---

# meeting_prep

Runs when **`meeting.upcoming`** happens, in 1 step. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `meeting.upcoming` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | `prepare` |
| **rules it honours** | [`prep_and_invite_mail`](../POLICIES.md#prep_and_invite_mail) |

## The steps, in order

### 1. `prepare_meeting`

The front door of the loop whose back door is email_minutes: one short note asking whether they want to walk in ready, carrying `?ask=prep&meeting=<ref>`.

- **reads:** refs.{organizer|person, title, start, uid?, meeting_id?, url?}
- **effect:** one notification
- **result:** {message_id, meeting_ref}
- **domains:** without **agent** the reaction ends there, saying so · without **meetings** the reaction ends there, saying so
- **mails:** `prepare`
- **rules it honours:** [`prep_and_invite_mail`](../POLICIES.md#prep_and_invite_mail)

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<details>
<summary>view source — <code>prepare_meeting</code></summary>

```python
@reg.step(needs=("agent", "meetings"))
def prepare_meeting(ctx: StepCtx):
    """The front door of the loop whose back door is email_minutes: one short note asking
    whether they want to walk in ready, carrying `?ask=prep&meeting=<ref>`.

    A TEMPLATE — substitutions only, no agent turn — read from `_global/mail/prepare.md` so the
    wording is a file edit rather than a rebuild. Five lines, plain text, one link: a prepare
    mail that has to be read twice has already failed. Honours mail_prep exactly as
    email_minutes honours mail_minutes.

    IT NEVER GOES TO A STRANGER. Founder, 2026-09-02, on a pre-meeting fan-out across a room:
    *"I am afraid this will not work for a 50 attendee meeting."* Before the meeting there is
    nothing yet to justify a mail to somebody who has never heard of us — and the recipient of
    this one is the organiser, or a person who is already a user. A stranger meets Vexa AFTER
    their meeting, in the attendee follow-up, which is why that mail carries the introduction
    and this one does not. Relatedly: this mail must never claim a workspace was started for
    anyone. Nothing is built for a person who has not clicked.

    Reads: refs.{organizer|person, title, start, uid?, meeting_id?, url?}
    Effect: one notification · Result: {message_id, meeting_ref}."""
    to = ctx.refs.get("person") or ctx.refs["organizer"]
    # `person` is set when this fires for somebody other than the organiser. Only mail them if
    # they ALREADY have an account: ensure_platform_user would create one, and a prepare mail
    # is not a good enough reason to mint an identity for a person who has not asked for it.
    if ctx.refs.get("person") and ctx.refs["person"] != ctx.refs.get("organizer"):
        existing = p.platform_user_id(to)
        if not existing:
            return Done({"skipped": "not a user yet — a stranger meets Vexa after the meeting, "
                                    "not before it", "to": to})
        uid = str(existing)
    else:
        uid = str(ctx.refs.get("uid") or (ctx.prior.get("ensure_user") or {}).get("uid")
                  or p.ensure_platform_user(to))
    # THE DEPLOYMENT'S RULE BEFORE THE PERSON'S PREFERENCE (Vexa-ai/vexa#1615).
    # `prep_and_invite_mail` in `_global/POLICIES.md` is the admin's answer for everybody; the
    # `mail_prep` setting under it is one person's answer for themselves. Deny wins either way,
    # and an unreadable `_global` resolves to the default, which is on.
    if not p.policies.read(uid).get("prep_and_invite_mail",
                                    p.policies.DEFAULTS["prep_and_invite_mail"]):
        return Done({"skipped": "prep_and_invite_mail is off for this deployment"})
    if not p.setting(uid, "mail_prep"):
        return Done({"skipped": "mail_prep is off for this person"})
    title = ctx.refs.get("title") or "your meeting"
    ref = str(ctx.refs.get("meeting_id") or "")
    if not ref and ctx.refs.get("url"):
        # PLAN it, do not merely address it. The link used to carry the native id because the
        # row is minted at dispatch — so the prep chat opened on a Zoom number, held nothing
        # under it, and reached for the only meeting it could find. dispatch_bot claims this
        # same row at start-2min, so nothing downstream forks.
        ref = p.mt.ensure_meeting_row(uid, ctx.refs["url"], ctx.refs.get("title"),
                                      ctx.refs.get("start"))
    if not ref:
        raise StepError("nothing to link to — refs carry neither meeting_id nor url",
                        retryable=False)
    # THE MEETING'S PAGE EXISTS FROM THE MOMENT ITS ROW DOES (Vexa-ai/vexa#1601).
    #
    # A meeting that arrives from the MAILBOX has no chat, so nothing sends a bot from a
    # conversation and nothing binds one — the route agent-api mints on. This is that moment for
    # this half of the product: the row was just planned above (`ensure_meeting_row`), and the
    # organiser's document is minted onto their desk before the mail that links to it goes out.
    # agent-api records the path on the row, which is why `_scaffold_refs` below now CARRIES the
    # real path rather than composing one that nothing had yet written.
    #
    # It never raises (`ag.mint_meeting_note` swallows and returns ""), because a prepare mail
    # with a link is worth more than a page, and the page still arrives when the meeting ends.
    p.ag.mint_meeting_note(uid, ref)
    subject, body = p.mailtext.render("prepare", uid, {
        "title": title, "when": p._their_clock(uid, ctx.refs["start"]),
        "organizer": ctx.refs.get("organizer") or "",
    })
    # THE SCAFFOLD (PRD §5.5). The row was planned above precisely so this link can name it;
    # the mint is the last check that the chat behind the button will hold the meeting, and it
    # RAISES rather than mailing a prepare note whose button opens a chat that knows nothing.
    link = p.mint_scaffold("prep", to, opening="prep", meeting_id=ref,
                           refs=p._scaffold_refs(ctx, uid, meeting_id=ref),
                           provenance={"flow": "meeting_prep", "step": "prepare_meeting",
                                       "reaction_id": str(getattr(ctx, "reaction_id", "") or ""),
                                       "minted_by": str(uid)})
    mid = p.notify(to, subject or f"Prepare: {title}", body, link=link)
    p.mx.register_thread(db, mid, uid, f"meet-{ref}")
    return Done({"message_id": mid, "meeting_ref": ref}, provider_ref=mid)
```

</details>
