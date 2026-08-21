- **Self-host: `DEFAULT_BOT_NAME` now names the bots the terminal sends (#1258).** The terminal hardcoded
  `bot_name: "Vexa"` on every join, so the variable compose, Lite and helm already passed to
  meeting-api could never take effect. The terminal now omits `bot_name` and lets the deployment
  decide, making a rename a `.env` edit plus a meeting-api restart — no terminal rebuild. The stock
  name is unchanged (`Vexa`), an explicit `bot_name` in `POST /bots` still wins, and
  `NEXT_PUBLIC_DEFAULT_BOT_NAME` is now a real terminal build arg for baking a name into the image.
  See [Configuration](/configuration#bot-participant-name).
