- **Pre-active teardown attribution: lobby waits are no longer filed as join errors (#598).** A bot
  whose workload is torn down while it sits in `awaiting_admission` now terminates with
  `completion_reason: awaiting_admission_timeout` ("the room never admitted the bot") instead of the
  generic `join_failure`; bots destroyed at `requested`/`joining` keep `join_failure`. Both reasons
  stay transient→retry, so only the label — what operators see on the meeting terminal — changes.
