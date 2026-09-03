- **Jitsi bots join and transcribe JaaS/8x8.vc-backed embeds, including Brave Talk (#1513).**
  Deployments that render the whole conference — prejoin, lobby, media elements — inside a
  cross-origin `<iframe>` (rather than at the page's top level, as stock `meet.jit.si` and most
  self-hosted deployments do) previously left the bot stuck on the prejoin screen forever, or
  joined silently with zero transcript. Join, admission, and capture/recording now scan child
  frames alongside the top one. Send `platform` and `meeting_url` explicitly for these embeds —
  bare-URL auto-detection doesn't yet recognize non-8x8.vc hosts like `talk.brave.com`
  ([#1512](https://github.com/Vexa-ai/vexa/issues/1512)). See [Send a bot](/how-to/send-a-bot).
