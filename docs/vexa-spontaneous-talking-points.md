# Vexa — Spontaneous Presentation Notes
## FINOS AI Readiness Meeting, 2026-03-31

---

### OPENER (connect to what just happened)

"So you just saw two incredible solutions — INSIGHT turning audit reports into actionable intelligence, and BSD turning security logs into natural language queries. Both teams solved the same fundamental problem: **unstructured data → structured, queryable knowledge**.

We did the same thing — but for **meetings**."

---

### THE PROBLEM (30 sec)

"Jake just said something perfect: 'I see everything as a data problem.' Meetings are the biggest unsolved data problem in enterprise. You're in one right now. Everything said in the last hour — the WMATA fraud scenarios, the BSD architecture, Chamindra's question about guardrails — it's already gone. It lives in people's heads. Maybe someone took notes."

---

### THE INSIGHT (30 sec)

"Code works with AI agents because it's **connected, concise, and executable**. A function links to its dependencies. 3 lines do real work. You run it and it either works or fails.

Meeting knowledge is the opposite. 'John mentioned the Q3 numbers look soft' — who's John? Which numbers? Where's the source?

**Our insight: make meeting data look like code.**"

---

### THE STRUCTURE (30 sec)

"Just like a codebase has `models/` and `services/`, we extract meetings into:

```
knowledge/
├── entities/       # contacts, companies, products
│   ├── contacts/   # Mark Chen — CTO @ Acme
│   └── companies/  # Acme Systems — Datadog competitor
├── outputs/        # action items, minutes, emails
└── README.md
```

Same shape. Same operations: read, write, execute. The AI agent doesn't need new skills — just new data."

---

### CONNECT TO THIS MEETING (killer demo moment)

"In fact — this meeting. Right now. Our bot is in this call. It already knows:
- Luca opened with the antitrust notice
- Karl asked for 3 things: indicate interest, contribute, give roadmap feedback
- WMATA's tool ingested 100+ reports, 803 findings
- Tony mentioned Replit, Express, PostgreSQL, AWS Bedrock, Claude Sonnet
- BSD has 5 years of security data, breach detection gap went from 90 to 116 days
- Chamindra asked about model validation and guardrails
- Jake emphasized OCSF, Open Semantic Interchange, model-agnostic design

That's not notes. That's structured knowledge, queryable by any AI agent."

---

### ARCHITECTURE (30 sec)

"Under the hood:
- **Meeting API** — manages bots in live meetings (Google Meet, Teams)
- **Agent API** — Claude Code / Cursor / any agent reads and writes the knowledge workspace
- **Runtime API** — enterprise infrastructure: containers, isolation, scaling

The bot joins your meeting. The transcript becomes structured entities. Any AI agent can query it."

---

### THE ASK (same format as other presenters)

"Three things, same as Karl asked:
1. **Interest** — if meeting intelligence matters to your org, let us know
2. **Feedback** — what would make this useful for YOUR meetings?
3. **Try it** — we can drop a bot into your next FINOS call and show you the knowledge it extracts"

---

### CLOSER

"Jake said 'giving smart people the tools to do their job easier.' That's exactly it. Except the data source isn't logs or audit reports — it's the meetings you're already having."
