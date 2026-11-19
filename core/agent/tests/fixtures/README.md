# Test fixtures

- `github_create_recorded.json` — the GitHub repository-creation requests (method, URL, headers, body)
  agent-api sent before the git provider port existed, recorded from that code with a placeholder
  token. `tests/test_git_provider_port.py` replays each case through the GitHub adapter and requires
  the same bytes, so the port changes nothing GitHub sees.
