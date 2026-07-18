- **Google Meet segments now carry a real `language` (#523).** The Meet lane stamps the
  STT-detected (or forced) language of each transcription window onto its segments, so
  `GET /transcripts/...` labels Meet segments the same way Zoom/Teams always did — mixed-language
  meetings read truthfully instead of `null`. The language contract (auto vs forced mode,
  window-level granularity) is now documented in [Send a bot](/how-to/send-a-bot) and the
  [Meetings API](/api/meetings#send-a-bot-to-a-meeting).
