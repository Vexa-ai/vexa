- **Meeting-header bot controls follow the live WebSocket, never a stale snapshot (#674).** When
  the `meeting.status` stream is disconnected the header shows "Reconnecting…" and Stop/Send bot
  are disabled — a stale-live row can no longer offer an actionable "Stop bot" for a meeting the
  backend no longer has. A stop that races reality (404/409) now shows a human message ("This
  meeting is no longer active — refreshing the list.") and reconciles the control, instead of a
  raw JSON toast.
