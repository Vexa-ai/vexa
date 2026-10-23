- **LLM spend cap (#1783).** Agent turns are refused before dispatch once the deployment or the
  person has spent its daily or monthly AI budget (`_global/POLICIES.md`: `llm_budget_org_daily_usd`
  25, `llm_budget_org_monthly_usd` 250, `llm_budget_user_daily_usd` 5, `llm_budget_user_monthly_usd` 50;
  `off` removes one). Spend is counted from each model call's reported cost or tokens; the admin is
  notified at 80% and 100%. `VEXA_LLM_KILL_SWITCH=1` (Helm `agentApi.llmKillSwitch`) refuses every turn.
