- **Restarting mid-leave no longer strands a meeting in "stopping" (#1280).** When Vexa restarted while a
  bot was still leaving, the meeting could sit in "stopping" — shown as active — indefinitely across a
  redeploy burst. A meeting whose stop was explicitly requested now converges on the short stop grace.
