# Workspace templates — what a new workspace can start as

Each directory is one template: a `TEMPLATE.md` manifest plus the pages a new workspace is born
with. They are seeds. At boot they are copied into `_global/workspace-templates/` when that
directory does not already have them, and never overwritten after that: the company's copy is the
admin account's to edit from chat, with the history and rollback every `_global` page has.

A person can also keep their own templates in their Personal workspace under
`templates/workspaces/<name>/` ("save this workspace as a template"). Theirs win over the
company's when the names match.

## The manifest

```markdown
---
title: Event organization
description: One line shown in the picker.
when: When to use it, in a sentence.
---
## Creates
- overview.md — what the page is for

## Questions
- `event_name` — What is the event called? (default: Our event)

## Suggested routines
- **Weekly organizer digest** — what it would do. A suggestion only; nothing is switched on.
```

Pages may use `{{key}}` for any question's answer, plus `{{workspace_name}}` and `{{today}}`.
An unanswered question without a default renders as `_(to decide)_`.
