# /api/minutes/assign — local-dev assignment seam

Records a meeting→group binding (`{uid, workspaceId}` → `/tmp/minutes-bindings.jsonl`) so the
organiser's *assign to a group* click completes a loop in dev. 404s outside `NODE_ENV=development`
+ minutes mode. The production binding store and the group re-run it triggers are P4.
