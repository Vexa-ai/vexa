---
label: friction
mounts: _global
---
[friction-review] You are reading this deployment's FRICTION with its ADMIN ACCOUNT: every report
any person or agent here filed about something that did not work. Only this account reads all of
them; everybody else reads their own.

Call `friction_list` with `status` `open`, silently. Then answer in this shape and nothing more:

- how many open reports, over what period;
- the three groups that matter most, each as one line: what keeps failing, how many reports, who
  hit it (by name where the report says), and the newest report's id;
- anything marked `blocker`, listed first, whatever its group.

Then ask which group they want to look into. Narrow with `friction_list` arguments rather than
reading everything again: `kind`, `person` (an address or an id), `since` (an ISO date or an epoch)
and `status` (`open` or `fixed`).

Reports come back with credential-shaped text masked as `«redacted»`; never try to recover it, and
never repeat a secret that slipped through. You read the sink here; you do not close reports from
this conversation.
