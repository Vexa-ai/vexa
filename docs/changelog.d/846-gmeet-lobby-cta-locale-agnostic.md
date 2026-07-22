- **Google Meet: the bot joins a lobby in any UI language (#846).** A Meet lobby served in a
  non-English language whose join button carries an accessibility label matched none of the bot's
  join selectors, so the bot sat on the lobby for the full 60s budget and exited with zero
  transcript segments. The lobby's primary button is now also located structurally — the one
  visible, enabled button with a real text label and no icon — which needs no knowledge of the UI
  language. The scan resolves nothing unless exactly one button qualifies, so it can never click
  the wrong control. When the button genuinely cannot be found, the failure now records the
  observed URL, page language and visible button labels, so the reason reaches you in the meeting's
  error instead of only in the bot's logs.
