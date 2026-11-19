- **Budgets and model policy live in the company's policy file (#1783).** `_global/POLICIES.md` now
  holds the token budget per person per rolling day and the share background work may use, routine
  quotas (read live by flows), daily caps for the home and event-started runs, and which models are
  allowed and used by default for chat, routines, the home and event-started runs (`models_for`
  replaces a catalog entry's `allowed_subjects`). Edits apply on the next turn; environment
  variables are only install defaults; a person may set stricter limits for themselves. Usage is
  counted from each turn's reported tokens. Chat warns at 80% and stops at 100% ("ask your admin");
  background work pauses with the reason on the bell and in its chat. Admins get a one-line usage
  summary per person (`usage_summary`), and anyone can ask for their own limits (`my_limits`).
