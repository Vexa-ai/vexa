- **Standing notices ride along, and refusals arrive as structure (#1546, #1551).** An item whose
  copy declares itself a standing notice now travels on the meeting tools' own results — and on
  their refusals — so an agent hears it without going looking; `GET /queue/notices` asks for just
  those sentences. A refused tool call reaches the agent as fields (`reason`, `message`,
  `action_url`, the upstream body) instead of prose with JSON inside it. **Anything scraping the old
  single-sentence error text needs updating.**
