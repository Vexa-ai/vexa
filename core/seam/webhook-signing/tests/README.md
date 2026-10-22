# webhook-signing seam tests

`test_seam_webhook_signing.py` runs the real gateway, admin-api and meeting-api and checks the signature on the `meeting.completed` delivery. `conftest.py` puts the three services on `sys.path` and provides the Postgres URL.
