# service_authority

The meeting-api-owned, policy-free seam for asking an independently deployed
authority whether one bot session may be admitted or continue through a
one-minute service boundary.

Public surface: `meeting_api.service_authority`.

The module may depend on Python's standard library, `httpx`, the published
`service-authority.v1` wire contract, and injected meeting/runtime ports. It
must not know about payment providers, prices, balances, customer records, or
hosted plan policy. With no authority configured it uses the explicit
allow-all adapter, preserving the OSS self-hosted behavior.
