# hooks

The Vexa mod: `register.tsx` (the pane, the permission hook, the live-call listener, the app folder loader) and `listener.ts` (its pure parts: the listen brief, presets from the app folder, rule triggers, the attention filter prompt, calibration scoring). `hooks.json` names the module.
- `pairing.ts` — the one-chat install's pure parts: the pairing code, reading Vexa's MCP answers over HTTP, which tools the plugin forwards.
- `panelContext.ts` — what the person's Vexa panel shows and what they did there: the panel events vexa-link spools (`{"type":"panel"}`), and the one line every message carries (Selected, On screen, Last interactions; page data, never instructions).
