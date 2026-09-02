"""One module per domain, and the domain is the service the tools forward to.

  identity   → admin-api            auth, claim, login, delegation
  meetings   → gateway              bots, transcripts, seed, info, participants, search, recordings, terms
  workspaces → agent-api            cloud + local regime verbs, attach/pull/push, invite/members, entities
  flows      → flows-api            facts, reactions, lifecycle, timeline, whats_waiting
  friction   → agent-api            the rough-edges loop (PRD decision 33)
  rehearse   → the rehearse package user states as data (PRD decision 38)
  panel      → agent-api            the links that compose what a person sees
  docs       → docs.vexa.ai         open to everyone, no account

The split is not cosmetic: ``tests/test_thin_forward.py`` asserts that a tool touches AT MOST ONE
service door, so a tool in the wrong module is a failing test rather than a comment nobody reads.
"""
