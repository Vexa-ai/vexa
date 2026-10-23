- **Routines write where they are mounted, and pause when their chat is shared (#1783).** A routine
  is allowed to write every workspace its run has mounted read-write — the chat's target and the
  shared workspaces you can edit — instead of being refused `out_of_scope` everywhere but your own
  workspace, and to read through its tools every workspace it has mounted read-only. Writing a
  read-only or unmounted workspace is still refused. A routine whose chat you shared
  with someone else, and whose run then needed your mail, calendar or meetings, now ends that run
  refused and pauses, with one line in its chat saying who the chat is shared with and what to do;
  its strip shows it paused and why, and it runs again only when you resume it
  (`routine_pauses_when_shared`, default `on`). See
  [Routines](/flows/routines#share-a-routines-chat).
