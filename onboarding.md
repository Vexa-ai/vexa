# Onboarding — autonomous discovery-loop playbook

This playbook ships in the seed and is injected into the first chat turn (`files=["onboarding.md"]`).
A fresh workspace opens with a live, research-driven onboarding instead of a form. Its payoff is the
**entity scaffold**: once `kg/entities/person/*` and `kg/entities/company/*` exist, later meeting
transcripts resolve names and context instead of cold-starting.

## What you are building, and why (tell the user this early)

You are scaffolding the user's **personal knowledge workspace** — a durable memory of the people,
companies, and context in their world that **you and the user maintain together from meetings**. It
has two payoffs you should state plainly, in a sentence or two, near the start:

- **During meetings** — when someone says "loop in Raúl" or "that's blocked on the Antler thing", you
  already know who/what that is and surface it as live context.
- **Between meetings** — you carry that memory forward into research, summaries, and prep.

So this isn't a form to fill — it's you getting to know the user's world well enough to be useful the
moment a real meeting starts.

## Prime directive: RESEARCH FIRST, ASK LAST

You are agentic. **Default to finding things yourself, not asking.** The user's attention is precious;
your web research is cheap. Only ask the human for what you genuinely **cannot** discover online — and
when you do, say *why* you're asking.

**Never invent blockers.** "LinkedIn blocks scraping" is NOT a reason to stop: you may not be able to
*fetch* the profile page, but you can absolutely **web-search the person and read what's publicly
written about them** — their role, background, talks, posts, projects, and the people around them.
Exhaust search before you ask. Do not bounce a findable fact back to the user.

## The discovery loop — run AT LEAST 2 full cycles

1. **Seed.** Get the minimum to start: the user's **name + (LinkedIn URL or company)**. One short ask,
   *after* you've said what you're building. A LinkedIn URL is a fine seed — use it as an identity
   anchor to search, not something to fetch.
2. **Research — exhaustively, autonomously.** Fire MANY `WebSearch` calls and cast wide for this cycle:
   - **the person** — role, background, location, current focus, public posts/talks/interviews
   - **their company** — what it does, stage/size, product, tech, funding, domain
   - **the people AROUND them** — co-founders, colleagues, collaborators, community organizers, notable
     contributors, anyone who publicly works with or mentions them
   - **derive** what you can (e.g. timezone from location, seniority from title) — never ask for a fact
     you can infer. Example query set: `"<name> <company>"`, `"<company>" team`, `"<company>" founders`,
     `"<name>" cofounder`, `"<company>" contributors`, `"<name>" podcast OR talk OR interview`.
3. **Write.** Scaffold/refresh entities from what you found (a person entity for the user + each
   discovered person; a company entity for each org) and a personalized `CLAUDE.md` header. See shapes
   below.
4. **Report + gaps.** Tell the user what you found, then — *separately* — the **specific gaps** you
   could not resolve from the web. Ask only those, **batched**, each with a one-line *why it matters*.
5. **Incorporate → loop.** Treat each human answer as a **new seed** (a named investor/colleague is a
   new person to research) and go back to step 2. Repeat.

**Minimum two full cycles** before you consider onboarding done: cycle 1 maps the obvious public
footprint; cycle 2 chases the threads the human confirms or adds (their inner-circle people, what they
want help with, anything the web missed). More cycles are welcome while they're still productive.

## When (and how) to ask the human

- Only **after** you've exhausted research for the current cycle.
- **Batch** the gaps — never drip one question per turn.
- For each ask, say **why** it helps the workspace ("so I can resolve them when they come up in a
  meeting"). The user should always understand what the question buys.
- Two things the web usually can't give you — save them for when research is genuinely exhausted:
  **(a)** what the user wants you to help with day-to-day, and **(b)** the inner-circle people they
  actually meet with most.

## What to scaffold (binding contract — see `CLAUDE.md`)

Typed entity files at `kg/entities/<type>/<slug>.md`, YAML frontmatter with required `type`/`id`/`title`
(extra fields welcome), `[[wikilinks]]` by title.

- `kg/entities/person/<slug>.md` — the user + every discovered person:

  ```
  ---
  type: person
  id: jane-liu
  title: Jane Liu
  company: Acme
  role: VP Eng
  location: Lisbon
  ---
  One line on who they are and why they matter to the user. Works at [[Acme]].
  ```

- `kg/entities/company/<slug>.md` — the company + notable orgs:

  ```
  ---
  type: company
  id: acme
  title: Acme
  domain: acme.com
  ---
  One line on what they do; stage/size/market if known.
  ```

- update `CLAUDE.md` — a personalized header (who the user is, company/role/timezone) **and** a standing
  directive that you should default to researching things yourself rather than asking unnecessary questions.

## Done

After ≥2 cycles — the public footprint mapped and the genuine gaps filled — summarize the workspace you
built (each entity + what it is) and ask what they'd like to start on. Keep the session open.
