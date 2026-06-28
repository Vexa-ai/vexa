---
enabled: true
model: openrouter/free               # must be in the allowlist; else falls back to the default + logs
cadence_segments: 4                  # run a copilot beat every N completed segments (or on a new speaker)
card_kinds: [person, company, product, number, decision, action-item, question, claim]
write_meeting_doc: true              # author the post-meeting kg entity on session_end
# ── Workspace-GOVERNED policy (prompt-only governance) ──────────────────────────────────────────────
# These two rules are the live POLICY for the copilot. The MECHANISM (transcript window + JSON shape)
# is in code; ask your agent to edit these to change cleanup/tagging behavior — no redeploy needed.
polish_rules: >
  ALWAYS write each line in the FIRST PERSON, attributed to the speaker's meaning ("I..."); for plain
  facts, state the fact directly ("Anthropic released..."). Apply LIGHT readability cleanup ONLY: dedupe
  overlapping/repeated lines, fix punctuation and capitalization, and merge fragments into readable
  sentences. This is NOT a heavy semantic rewrite or summary — preserve every fact, the speaker's
  wording, uncertainty, and tone. Remove filler, false starts, and obvious transcript/model artifacts.
  Do NOT invent missing content. Never write observer boilerplate ("Speaker says", "the speaker
  describes", "they talk about").
tag_rules: >
  Extract two kinds of tags from THESE lines and mark them actionable. (1) ENTITIES: person, company,
  product, and any concrete number. (2) SIGNALS: decision, action-item, question, and claim. Surface
  only what is concretely present in the lines — do not invent.
---
<!-- Steering for the live meeting copilot — natural language, what to watch / ignore / tone.
     This whole body is merged into the copilot prompt. Edit it to tune behavior. -->
Watch for people, companies, products, decisions, action items, questions, and claims. Keep the transcript neutral, concise, and free of topic headings.
