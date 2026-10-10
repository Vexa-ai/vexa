---
kind: flow
flow: meeting_share
version: 1
trigger: meeting.shared
steps: 1
generated: from the code that runs it — edits here are overwritten
---

# meeting_share

Runs when **`meeting.shared`** happens, in 1 step. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `meeting.shared` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | `meeting-share` |
| **rules it honours** | none |

## The steps, in order

### 1. `mail_meeting_share`

Mail one person the link to a meeting its owner shared with them.

- **reads:** refs.{email, uid, meeting_id, title?, inviter?}
- **effect:** one restricted grant, one notification
- **result:** {message_id, to, meeting_id}
- **domains:** without **meetings** the reaction ends there, saying so
- **mails:** `meeting-share`

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<ViewSource step="mail_meeting_share">

```python
@reg.step(needs=("meetings",))
def mail_meeting_share(ctx: StepCtx):
    """Mail one person the link to a meeting its owner shared with them.

    A person pressed **Invite** in a meeting's Share dialog with this address, so the mail is
    sent on a human's own act — like `mail_workspace_invite`, and for the same reason no
    fan-out switch may swallow it.

    THE LINK IS MINTED HERE, AT SEND TIME, and composed from this deployment's own `VEXA_UI_URL`.
    The fact carries no credential (R1801-5): meeting-api keeps grants as hashes at rest, and
    the event store is not a second place to keep a working token. So this step mints a fresh
    grant RESTRICTED to this one address, as the owner (`uid`), through meeting-api — the same
    mint the attendee fan-out uses — and links the terminal's share arrival (`?tshare=`) to it.
    A forwarded mail admits nobody else, and the owner's access list shows it as one invite.

    The title is the owner's words and is ONE bounded line of plain text (R1801-2) — cut again
    here, whatever the producer sent.

    Reads: refs.{email, uid, meeting_id, title?, inviter?}
    Effect: one restricted grant, one notification · Result: {message_id, to, meeting_id}."""
    to = str(ctx.refs.get("email") or "").strip()
    uid = str(ctx.refs.get("uid") or "").strip()
    meeting_id = str(ctx.refs.get("meeting_id") or "").strip()
    if not to or not uid or not meeting_id:
        raise StepError(
            f"cannot mail the share of meeting {meeting_id or '<unknown>'}: refs carry "
            f"{'no address' if not to else 'an address'}, {'no owner' if not uid else 'an owner'} "
            f"and {'no meeting' if not meeting_id else 'a meeting'} — a share mail needs all three.",
            retryable=False)
    try:
        token = mt.mint_transcript_share(uid, meeting_id, to, expires_in_sec=30 * 86400)
    except mt.ShareMintError as e:
        # The owner deleted the meeting, or it is no longer theirs: a 4xx is a fact and is not
        # retried; a 5xx/429 is the platform having a moment and is. No token is in the text.
        raise StepError(f"no link could be minted for the share of meeting {meeting_id}: {e}",
                        retryable=bool(getattr(e, "retryable", False))) from e
    link = _common.ui_link(tshare=token)
    values = {}
    for k, cap in (("inviter", 254), ("title", 200)):
        v = _re.sub(r"[\x00-\x1f\x7f]+", " ", str(ctx.refs.get(k) or "")).strip()
        if v:
            values[k] = v if len(v) <= cap else v[:cap - 1].rstrip() + "…"
    values.setdefault("inviter", "Someone")
    values.setdefault("title", "a meeting")
    subject, body = mailtext.render("meeting-share", uid, values)
    if not subject:
        logger.warning("the meeting-share template carries no `subject:` line — falling back")
        subject = f"{values['inviter']} shared a meeting with you"
    mid = notify(to, subject, body, link=link)
    return Done({"message_id": mid, "to": to, "meeting_id": meeting_id}, provider_ref=mid)
```

</ViewSource>
