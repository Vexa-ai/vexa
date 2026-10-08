# tests

Offline tests for the broker: no network, no real keys. `conftest.py` builds a broker over a
temporary state directory with three fixture role keys and an in-memory store, and `signed(...)`
signs requests with the vendored signer exactly as the real callers do. `test_config.py` and
`test_store.py` use the real encrypted store.
