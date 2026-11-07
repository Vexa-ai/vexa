- **Notifications you can act on.** A workspace invite's notice now has **Accept** (you join and the
  workspace opens) and **Decline**; a shared or mentioning chat has **Open chat**; a meeting shared
  with you has **Open transcript**; a bot that joined your meeting has **Open**. One tap does it and
  marks the notice read in every tab. With your permission (asked once, from the bell), a notice that
  arrives while the tab is in the background shows as a browser notification with a soft chime. The
  list shows unread first under New and Earlier, with relative times. The agent can do the same with
  the new `notifications_act` tool. Meeting notices (shared with you, bot scheduled, bot joined) come
  from flows through agent-api's internal tier. Upgrade: re-run the session store's init
  (`agent-sessions-init`); it adds two nullable columns to `agent_session_notification` (`source`,
  `acted`) on a deployment that already had the table. See [Notifications](/minutes/notifications).
