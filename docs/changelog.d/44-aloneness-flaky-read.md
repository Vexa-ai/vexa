- **The notetaker no longer leaves a live meeting after a transient page error.** The
  alone-in-meeting watcher counts real participant tiles and leaves once the bot has been
  alone past the everyone-left timeout. Its counter caught *every* error and reported **0
  tiles** — which the watcher reads as "everyone left" — so a single flaky DOM read
  (navigation, teardown, a detached frame) could end a capture while people were still in
  the call. The watcher already had the right guard (a failed read resets the clock and
  never leaves); it just never fired, because the counter masked the failure as zero.
  Failed reads are now surfaced, so the guard engages. Admission is unchanged: there a
  failed read still means "no participant tile yet" and simply polls again.
