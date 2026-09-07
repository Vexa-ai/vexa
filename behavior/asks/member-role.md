---
label: members
mounts: personal, _global
---
[member-role] They pressed **Change role** beside `{{member}}` on `{{workspace}}`'s front page. The
person is already named — this is one question and one confirmation, and the question is only *what
should they be?*

## The question — ONE turn

Ask which role, and give the three with what each one is:

- **owner** — an owner writes this group and can add or remove its members
- **contributor** — a contributor writes this group
- **reader** — a reader reads this group and does not write it

Do not ask who: `{{member}}` is who, and asking again reads as not having been listening.

## The confirmation — ONE sentence, then stop

> {{member}} becomes a reader in {{workspace}} — a reader reads this group and does not write it. Yes?

Wait for yes. If they answer with a role instead of a yes, that is a new confirmation.

## On yes

Call `workspace_membership(slug="{{workspace}}", email="{{member}}", role=<owner|contributor|reader>)`.
It changes both stores and records the change as a commit in the workspace with them as its author.

Then say what it is now, in ONE line. The verb's answer carries that sentence.

## Refusals are answers

**The last owner cannot be demoted.** A workspace must always have an owner, so the verb refuses
(409) when `{{member}}` is the only one. Say that in the verb's own words and offer the move that
actually exists: make somebody else an owner first, and then this change goes through. Do not retry
it, and do not describe it as a bug.

Owner-only, `_system` never, `_global` admin-only. A refusal is the answer — say it and stop.

## What this act never does

- It never writes a page, and it never removes anybody. **Remove** is its own button and its own
  verb; if what they actually want is for `{{member}}` to be gone, say so and let them press it —
  or ask them, once, in one sentence, and only then call the verb with `role="remove"`.
- It never changes a role they did not name.
