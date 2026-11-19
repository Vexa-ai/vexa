# platform/__tests__

Unit tests for the client substrate. `accountStorage` — browser state belongs to the account that
wrote it: one browser, two accounts in turn, and the second never reads what the first stored
(an open workspace, a pending meeting, a layout); unscoped legacy keys are cleared, never applied.
House style: pure functions over the jsdom Storage; vitest.
