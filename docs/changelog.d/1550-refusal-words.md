- **A refusal carries the deciding service's own words (#1550).** When a service declines a request
  it returns `message` and `action_url` alongside the reason, and the API and terminal render those
  verbatim rather than mapping the reason onto copy of their own. A deployment's refusals now say
  what that deployment means, not what this repo once guessed.
