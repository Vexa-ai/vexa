# tests — the offline proving ground

Zero domains, zero network, zero sleeps. `fixtures.py` = the deterministic rig (FakeClock +
sqlite + FakeWorld with fault dials) and the time-jumping `drain`. `loopback.py` = the world that
ANSWERS BACK (bot transcribes → webhook fact returns, redelivered 3×). Suites: the PRD §13 failure
matrix (`test_fixture_flows`), n8n shape coverage (`test_shapes`), hostile inputs/states
(`test_hostile`), the full round trip (`test_loopback`), and the randomized 6-invariant storm
(`test_storm`; `STORM_SEED=n` reproduces a failing run exactly).

The **onboarding** pair runs on a second rig in `conftest.py` (`db` / `clock` / `registry`) — the
real `flows_defs.production.build`, no `FakeWorld` — because both suites are about what a
deployment composes rather than about how a step behaves under fault:
`test_onboarding_flow.py` (who the row is about, and that activation is a transcript rather than a
meeting) and `test_flow_packs.py` (the three seams a private flow pack plugs into —
`VEXA_FLOWS_DEFS_EXTRA`, `$VEXA_BEHAVIOR_DIR/queue/`, and an intake with no carrier allow-list).
That `registry` fixture is what blanks the domain doors `OFFLINE_DOORS` declares above; it is
deliberately not autouse, so `test_no_agents.py` keeps owning its own unset.
