- **Meetings can now be recorded on video, not just audio (`RECORDING_VIDEO_ENABLED`).** A recording
  spawn captures the bot's own view of the meeting with ffmpeg alongside the existing audio, stored
  and served through the same recording endpoints (`GET /recordings/{id}/master?type=video`). **Off
  by default** — video is roughly 20-50x the size of the audio. See
  [Configuration](/configuration).
- **Retention is now expressible without a delete service.** Every stored recording object is tagged
  `media=audio` or `media=video`, so a policy like "keep audio forever, expire video after 90 days"
  is a one-line bucket lifecycle rule. Nothing expires by default; the tag is the mechanism, the
  rule is yours. See [Configuration → Retention](/configuration#retention-nothing-expires-until-you-say-so).
