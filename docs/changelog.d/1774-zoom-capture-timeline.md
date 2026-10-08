- **Zoom: transcripts of a starved call keep their timestamps and long turns keep their words (#1774).**
  Segment times now come from each captured frame's own timestamp, so speech late in a long turn is no
  longer placed tens of seconds early. A pause of up to 2 s no longer splits a Zoom turn, and a turn
  that reaches the 30 s limit is cut at a quiet moment instead of losing the audio after its last request.
