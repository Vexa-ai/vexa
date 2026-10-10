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

- **reads:** refs.{email, token, uid, meeting_id, title?, inviter?, workspace_invite?}
- **effect:** one notification
- **result:** {message_id, to, meeting_id}
- **domains:** reaches no other domain
- **mails:** `meeting-share`

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<ViewSource step="mail_meeting_share">

```python
@reg.step
def mail_meeting_share(ctx: StepCtx):
    """Mail one person the link to a meeting its owner shared with them.

    A person pressed **Invite** in a meeting's Share dialog with this address, so the mail is
    sent on a human's own act — like `mail_workspace_invite`, and for the same reason no
    fan-out switch may swallow it: the dialog has already told the owner the mail went out.
    Unlike that one it does not ask whether the address has an account here — the owner chose
    to mail it, and the link signs either kind of person in.

    THE LINK IS COMPOSED HERE, from this deployment's own `VEXA_UI_URL`, and never by the
    producer or the template (`behavior/mail/README.md`: a template never writes a URL). It is
    the terminal's existing share arrival — `?tshare=` redeemed after sign-in, plus `?invite=`
    when the owner bundled the meeting's workspace — the same link the dialog's Copy link
    hands out, so the mail and the copied link can never disagree. The token only admits the
    address it was minted for, so a forwarded mail admits nobody else.

    Reads: refs.{email, token, uid, meeting_id, title?, inviter?, workspace_invite?}
    Effect: one notification · Result: {message_id, to, meeting_id}."""
    to = str(ctx.refs.get("email") or "").strip()
    token = str(ctx.refs.get("token") or "").strip()
    meeting_id = str(ctx.refs.get("meeting_id") or "").strip()
    if not to or not token:
        # Typed and terminal: the refs are frozen at admission, so a retry asks the same
        # unanswerable question. Neither ref is printed — the token is a credential.
        raise StepError(
            f"cannot mail the share of meeting {meeting_id or '<unknown>'}: refs carry "
            f"{'no address' if not to else 'an address'} and "
            f"{'no token' if not token else 'a token'} — a share mail without both asks "
            "somebody to do nothing.", retryable=False)
    link = _common.ui_link(tshare=token, invite=str(ctx.refs.get("workspace_invite") or ""))
    uid = str(ctx.refs.get("uid") or "")
    values = {k: str(ctx.refs[k]) for k in ("inviter", "title") if str(ctx.refs.get(k) or "").strip()}
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
