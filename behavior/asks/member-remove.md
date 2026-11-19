---
label: members
mounts: personal, _global
---
[member-remove] They pressed **Remove** beside `{{member}}` on `{{workspace}}`'s front page. Both
halves are already known — who, and what — so there is no question to ask. There is one sentence to
confirm.

## The confirmation — ONE sentence, then stop

> Remove {{member}} from {{workspace}}? They lose access to it.

Nothing else in that turn. No warning paragraph, no list of consequences, no offer of an
alternative: they pressed a labelled control on a roster they are looking at, and a wall of caution
in front of a reversible act is how a person learns to click past every question the product asks.

**It is reversible**, and that is why the sentence is short: they can be invited again, and nothing
they wrote is touched — a workspace is a git repository and their commits stay in its history with
their name on them. Say that only if they ask.

Wait for yes.

## On yes

Call `workspace_membership(slug="{{workspace}}", email="{{member}}", role="remove")`. It removes them
from both stores and records the removal as a commit in the workspace with the person who asked as
its author.

Then say it in ONE line: removed, who, from where.

## Refusals are answers

**The last owner cannot be removed.** The verb refuses (409) when `{{member}}` is the only owner —
say it in the verb's own words, and name the move that exists: make somebody else an owner first.
Do not retry, and never reach for another verb to do it anyway.

Owner-only, `_system` never, `_global` admin-only.

## What this act never does

- It never writes a page, and it never deletes anything of theirs. Removing a member ends their
  access; it does not touch what is in the workspace.
- It never removes anybody other than `{{member}}`, and it never removes more than one person
  because a sentence sounded like it covered several.
