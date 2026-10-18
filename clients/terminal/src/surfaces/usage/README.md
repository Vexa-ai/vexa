# usage/

What a person sees of Vexa credits and model levels. Both are behind switches that are off by default and not included in `all`.

- `Usage.tsx`, feature `usage`:
  - `UsageMeter`: the "42% · 5h · 18% · week" chip beside the account. Its popover shows both windows, when each resets, and "+ extra usage".
  - `UsageLimitCard`: what the chat draws for a turn refused because a window is full. It shows when the window is back, "+ extra usage" and "Wait", then "Send again".
  - Reads `GET /api/usage`. "+ extra usage" is `POST /api/usage {extra: true}`. The switch is only recorded; nothing is charged.
- `Level.tsx`, feature `levels`, hosted plans only: `LevelPicker` takes the model picker's place in the composer.
  - A new chat picks 1–5.
  - After the first message the level is locked, shown as a "Level N" badge. Changing it means an explicit "Switch (resets the cache)".
  - Reads `GET /api/models/levels`. Picking is `POST /api/chat/level`.

The approved mock is `desktop/proto/Usage.tsx` and `Level.tsx` on the lab branch proto-on-product. The server side is agent-api `control_plane/credits.py`, `model_router.py` and `routers/usage.py`/`levels.py`.
