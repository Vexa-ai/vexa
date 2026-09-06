---
kind: flow
flow: workspace_invite
version: 1
trigger: workspace.invited
steps: 1
generated: from the code that runs it — edits here are overwritten
---

# workspace_invite

Runs when **`workspace.invited`** happens, in 1 step. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `workspace.invited` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | `workspace-invite` |
| **rules it honours** | none |

## The steps, in order

### 1. `mail_workspace_invite`

Carry one membership invite to somebody who is NOT on this instance.

- **reads:** refs.{email, link, uid, workspace, workspace_name?, role?, role_sentence?, inviter?}
- **effect:** one notification
- **result:** {message_id, to, workspace}
- **domains:** reaches no other domain
- **mails:** `workspace-invite`

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<ViewSource step="mail_workspace_invite">

```python
@reg.step
def mail_workspace_invite(ctx: StepCtx):
    """Carry one membership invite to somebody who is NOT on this instance.

    THE FLOW LIVES IN THIS MODULE FOR `desk_setup`'s REASON, NOT FOR THIS STEP'S. The step
    itself would run anywhere — see the `needs=` note above. What is agent-shaped is the
    PRODUCER: `workspace.invited` comes off the control plane that mints the invite, so in a
    deployment with no agent domain the fact is never published and a flow registered for it
    would sit in the registry doing nothing, in every such deployment, forever.

    AND IT ONLY EVER CARRIES AN OUTWARD ONE. agent-api publishes this fact only for an address
    that is EXTERNAL to this instance; a person who already has an account here is handed the
    link in the chat that invited them, which is faster, needs no mailbox, and cannot be lost
    to a spam folder. So this step never has to ask whether the recipient is one of ours, and
    must never grow that question — the answer lives at the producer, where the account lookup
    already happened.

    Reads: refs.{email, link, uid, workspace, workspace_name?, role?, role_sentence?, inviter?}
    Effect: one notification · Result: {message_id, to, workspace}."""
    # NO POLICY GATE, AND THAT IS A DECISION, NOT AN OVERSIGHT (Vexa-ai/vexa#1632).
    #
    # `prepare_meeting` two steps up consults `prep_and_invite_mail` before it sends. This step
    # deliberately does not, and the reason is in that rule's own text: it governs *"the prep
    # note before a meeting, and the line that tells an organiser how to put Vexa in a meeting
    # of their own"*, and `POLICIES.md`'s enforcement table maps it to exactly one step
    # (`emit_prep`). It is a switch over mail WE decide to send about a meeting — a fan-out an
    # admin may not want their fifty-person rooms to produce. This mail is the opposite kind of
    # thing: a person opened a chat, was asked for an address and a role, read one sentence
    # back and said yes. There is no rule in `_global/POLICIES.md` about membership mail, and
    # borrowing one written for a fan-out would silently repurpose it.
    #
    # Swallowing this send would make the product lie. The agent has already told the inviter
    # the invitation went out; a policy that drops the mail underneath that sentence leaves one
    # person believing they invited somebody and the other never hearing from us, with nothing
    # anywhere saying which. If a deployment ever does want to refuse membership mail, that is
    # a NEW rule with its own name and its own paragraph about what it costs — and the honest
    # place to enforce it is at the invite itself, where the person is present to be told no.
    #
    # There is no per-person `setting()` here either, for a plainer reason: the settings this
    # engine reads are the RECIPIENT's, and by construction the recipient has no account on
    # this instance to hold one.
    to = str(ctx.refs.get("email") or "").strip()
    link = str(ctx.refs.get("link") or "").strip()
    workspace = str(ctx.refs.get("workspace") or "").strip()
    # TYPED AND TERMINAL, like every other refusal in this module. Not retryable because the
    # refs are frozen at admission: a retry would ask the same unanswerable question of the
    # same frozen row, every ten minutes, forever. Two refs and not five, because these two are
    # the only ones without which the mail cannot do its job — a mail with no address goes
    # nowhere, and a mail with no link asks somebody to do nothing. A missing role or inviter
    # makes a worse mail; a missing link makes a mail that is not one.
    if not to or not link:
        raise StepError(
            f"cannot mail the invite to workspace {workspace or '<unnamed>'}: refs carry "
            f"{'no address' if not to else 'an address'} and "
            f"{'no link' if not link else 'a link'} — an invite mail without both is a mail "
            f"that asks somebody to do nothing.", retryable=False)
    # THE COMPANY LAYER IS READ THROUGH THE INVITER, and it has to be: `{{company}}` and
    # `{{visibility}}` come from `_global`, which is reached by a uid this instance knows, and
    # the recipient is by construction not one of those. `uid` is the INVITER for exactly this
    # reason — they are the person this deployment can look a setting up for.
    uid = str(ctx.refs.get("uid") or "")
    # ONLY THE REFS THAT ARE THERE. `mailtext.render` leaves an unknown `{{token}}` STANDING
    # rather than blanking it, on purpose — a visible `{{role}}` in a test inbox is a bug
    # report and a silently empty sentence is not — so a producer that drops a ref is loud
    # instead of shipping "invited you as a : ." The two that must not travel:
    #   `workspace` — the SLUG. `render` already fills `{{workspace}}` with the product's word
    #     for a person's own space ("desk"); passing the slug under that name would overwrite
    #     it for this one template and quietly mean two different things in two mails.
    #   `link` — a template never writes a URL (`behavior/mail/README.md`): a link a template
    #     could write is a link anyone who can edit a file can point anywhere.
    values = {k: ctx.refs[k] for k in ("inviter", "role", "role_sentence")
              if str(ctx.refs.get(k) or "").strip()}
    # The display name, falling back to the slug — which is a name, just a worse one. agent-api
    # already sends the slug when there is no display name; this is the belt for a producer
    # that forgets, and it is a fallback rather than a token left standing because a mail whose
    # subject line has a hole in it is not deliverable prose.
    values["workspace_name"] = str(ctx.refs.get("workspace_name") or workspace)
    subject, body = p.mailtext.render("workspace-invite", uid, values)
    if not subject:
        # `mailtext._split` answers "" when a template lost its `subject:` line and says the
        # caller must supply one — an empty subject reads as spam. Same fallback and same
        # warning as `email_attendees`, because somebody editing the live file is the only way
        # to get here and they should be able to find out.
        logger.warning("the workspace-invite template carries no `subject:` line — falling "
                       "back to the workspace name")
        subject = f"You have been invited to {values['workspace_name']}"
    # THE LINK TRAVELS AS THE PORT'S ARGUMENT, never inside the body. `notify.compose` puts it
    # last and on its own paragraph because a URL buried mid-paragraph is a URL nobody clicks,
    # and a step that hands over a URL should not also decide its typography.
    mid = p.notify(to, subject, body, link=link)
    # NO `register_thread`, and this is the deliberate half of the decision rather than the
    # forgotten half. That row exists to route a REPLY into an ongoing conversation, by thread,
    # for the subject who owns it (`flows_integrations/mailbox.py`) — and there is no such
    # subject here. The recipient has no account on this instance, which is the precondition
    # for this fact being published at all; the only uid on the fact is the INVITER's, and a
    # row keyed to them would be one the intake refuses anyway, since a reply from anybody but
    # that subject is `THREAD_MISMATCH` and quarantined. It would be a row whose only possible
    # outcome is a quarantine entry. This mail also expects no reply: its answer is the click.
    return Done({"message_id": mid, "to": to, "workspace": workspace}, provider_ref=mid)
```

</ViewSource>
