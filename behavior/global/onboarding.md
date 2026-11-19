---
kind: curriculum
read_by: agent-api home (control_plane/home.py parse_curriculum), on every home composition
---

# Onboarding

What a new person is shown over time on the new-chat home, in order. The home offers **at most two
of these at a time**, each only once its `requires` steps are done; a step is **done when its
evidence is in the person's usage** — never because they clicked its card. A step dismissed as
"wrong" or "done already" is not offered again; a snoozed one comes back after a day. Later steps
are what a settled person sees as "Try" cards.

Edit freely: the home reads this file on its next composition. Each step is a `## <id>` heading
(lower-case, dashes) with these lines:

- `label` — the card's line, at most 60 characters
- `why` — one sentence the card shows under it (optional)
- `prompt` — what the card's button asks the agent, in the person's own words
- `requires` — step ids that must be done first, comma-separated (optional)
- `done when` — the evidence, comma-separated, all must hold. Vocabulary: `meetings>=N`,
  `calls>=N`, `voice_notes>=N`, `workspaces>=N`, `shared_chats>=N`, `routines>=N`, `mentions>=N`, `chats>=N`,
  `crm_records>=N`, `connection:google_calendar`, `connection:google_email`
- `icon` — calendar, mail, chat, clock, repeat, folder, doc, plane, bot, spark, people or mic
- `act` — `call` makes the card's button start a call with Vexa (a new chat, a one-person call)
  instead of asking the agent (optional)

## connect-calendar
- label: Connect your calendar
- why: Minutes can then prepare you for each meeting and send its bot on time.
- prompt: Connect my calendar so you can see my meetings.
- done when: connection:google_calendar
- icon: calendar

## first-meeting
- label: Send the bot to a meeting
- why: Paste a meeting link, admit the bot, and watch the words arrive while you talk.
- prompt: I want to try Minutes in a meeting. Help me send the bot to one now.
- done when: meetings>=1
- icon: bot

## connect-mail
- label: Connect your email
- why: Minutes can build your private knowledge workspace from the last 90 days of mail and calendar, with sources.
- prompt: Connect my email, then offer to build my knowledge workspace from the last 90 days of mail and calendar.
- done when: connection:google_email
- icon: mail

## think-out-loud
- label: Think out loud
- why: Start a call with Vexa; it becomes a page with its transcript.
- prompt: Start a call with me so I can think out loud.
- act: call
- requires: first-meeting
- done when: calls>=1
- icon: mic

## workspace
- label: Make a workspace for a project
- why: Drop a folder or name a project, and Minutes answers from everything in it.
- prompt: Help me make a workspace for one of my projects.
- requires: first-meeting
- done when: workspaces>=2
- icon: folder

## share-chat
- label: Share a chat with a colleague
- why: A shared chat keeps you, your colleague and the agent on the same page.
- prompt: How do I share this kind of chat with a colleague? Walk me through it.
- requires: first-meeting
- done when: shared_chats>=1
- icon: people

## routine
- label: Make something you repeat a routine
- why: A routine runs on a schedule and its result shows up on this home.
- prompt: Suggest a routine for something I do by hand often, and set it up if I agree.
- requires: first-meeting
- done when: routines>=1
- icon: repeat

## mention
- label: Bring a colleague in with @
- why: Type @ and a name in a shared chat to bring them into the conversation.
- prompt: Show me how @-mentions work in a shared chat.
- requires: share-chat
- done when: mentions>=1
- icon: chat

## crm-record
- label: Keep a record of a company you work with
- why: Minutes links meetings, mail and people to it, so the next prep starts from history.
- prompt: Help me start a record for a company I work with.
- requires: first-meeting
- done when: crm_records>=1
- icon: doc
