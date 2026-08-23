# tests — the offline proving ground

Zero domains, zero network, zero sleeps. `fixtures.py` = the deterministic rig (FakeClock +
sqlite + FakeWorld with fault dials) and the time-jumping `drain`. `loopback.py` = the world that
ANSWERS BACK (bot transcribes → webhook fact returns, redelivered 3×). Suites: the PRD §13 failure
matrix (`test_fixture_flows`), n8n shape coverage (`test_shapes`), hostile inputs/states
(`test_hostile`), the full round trip (`test_loopback`), and the randomized 6-invariant storm
(`test_storm`; `STORM_SEED=n` reproduces a failing run exactly).
